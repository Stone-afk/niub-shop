from pathlib import Path

import pytest

from agent_service.conversation_service import ConversationService
from agent_service.conversations import ConversationRepository
from agent_service.providers import ModelProtocolError
from agent_service.prompt_loader import PromptLoadError
from agent_service.recommendation_agent import RecommendationInvalid, RecommendationResult
from agent_service.plan_state import PlanState


class FakeRecommendationAgent:
    def __init__(
        self,
        result: RecommendationResult | None = None,
        *,
        error: BaseException | None = None,
    ) -> None:
        self.result = result or RecommendationResult("默认回答", ())
        self.error = error
        self.messages = []

    async def generate(self, messages):
        self.messages.append(list(messages))
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_repository(tmp_path: Path) -> ConversationRepository:
    repository = ConversationRepository(tmp_path / "agent.db")
    repository.initialize()
    return repository


def make_plan() -> PlanState:
    return PlanState.model_validate(
        {
            "plan_id": "plan-1",
            "goal": "推荐商品",
            "status": "completed",
            "current_step_id": None,
            "steps": [
                {
                    "id": "recommend",
                    "title": "输出推荐",
                    "status": "completed",
                    "depends_on": [],
                }
            ],
            "next_action": "submit_product_recommendation",
            "reason": "已完成",
        }
    )


@pytest.mark.anyio
async def test_builds_model_context_from_persisted_completed_turns(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)
    earlier = repository.create_turn(1, conversation.id, "第一问")
    repository.complete_turn(1, conversation.id, earlier.id, "第一答", ["old"])
    agent = FakeRecommendationAgent(RecommendationResult("第二答", ("new",)))
    service = ConversationService(repository, agent, "系统提示")

    stream = await service.open_turn(1, conversation.id, "第二问")
    assert [chunk async for chunk in stream] == ["第二答"]

    assert agent.messages[0][0]["role"] == "system"
    assert agent.messages[0][0]["content"].startswith("系统提示")
    assert "[response_language=zh]" in agent.messages[0][0]["content"]
    assert agent.messages[0][1:] == [
        {"role": "user", "content": "第一问"},
        {
            "role": "assistant",
            "content": '第一答\n\n[structured_recommendations]\n[{"position": 1, "product_id": "old", "source": "organic"}]',
        },
        {"role": "user", "content": "第二问"},
    ]
    stored = repository.get_conversation(1, conversation.id).turns[-1]
    assert stored.status == "completed"
    assert stored.recommended_product_ids == ("new",)


@pytest.mark.anyio
async def test_deletes_conversation_through_service(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)
    service = ConversationService(repository, FakeRecommendationAgent(), None)

    await service.delete_conversation(1, conversation.id)

    assert await service.list_conversations(1) == []


@pytest.mark.anyio
async def test_persists_latest_plan_state_with_completed_turn(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)
    agent = FakeRecommendationAgent(
        RecommendationResult("回答", (), (), make_plan())
    )

    stream = await ConversationService(repository, agent, None).open_turn(
        1, conversation.id, "推荐商品"
    )
    [chunk async for chunk in stream]

    stored = repository.get_conversation(1, conversation.id).turns[0]
    assert stored.plan_state is not None
    assert stored.plan_state["plan_id"] == "plan-1"
    assert stored.plan_state["status"] == "completed"


@pytest.mark.anyio
async def test_system_prompt_path_is_reloaded_for_each_turn(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)
    prompt_file = tmp_path / "system.md"
    prompt_file.write_text("系统提示 v1", encoding="utf-8")
    agent = FakeRecommendationAgent()
    service = ConversationService(repository, agent, None, prompt_file)

    first = await service.open_turn(1, conversation.id, "第一问")
    [chunk async for chunk in first]
    prompt_file.write_text("系统提示 v2", encoding="utf-8")
    second = await service.open_turn(1, conversation.id, "第二问")
    [chunk async for chunk in second]

    assert agent.messages[0][0]["content"].startswith("系统提示 v1")
    assert agent.messages[1][0]["content"].startswith("系统提示 v2")


@pytest.mark.anyio
async def test_missing_system_prompt_marks_turn_failed(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)
    agent = FakeRecommendationAgent()

    with pytest.raises(PromptLoadError):
        await ConversationService(
            repository, agent, None, tmp_path / "missing.md"
        ).open_turn(1, conversation.id, "问题")

    assert repository.get_conversation(1, conversation.id).turns[0].status == "failed"


@pytest.mark.anyio
async def test_buffers_result_in_fixed_chunks_and_commits_only_after_consumption(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)
    answer = "A" * 65
    stream = await ConversationService(
        repository,
        FakeRecommendationAgent(RecommendationResult(answer, ("one",))),
        None,
    ).open_turn(1, conversation.id, "问题")
    iterator = stream.__aiter__()

    assert stream.recommended_product_ids == ("one",)
    assert await anext(iterator) == "A" * 64
    pending = repository.get_conversation(1, conversation.id).turns[0]
    assert pending.status == "pending"
    assert pending.recommended_product_ids == ()
    assert await anext(iterator) == "A"
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)

    completed = repository.get_conversation(1, conversation.id).turns[0]
    assert completed.status == "completed"
    assert completed.assistant_content == answer
    assert completed.recommended_product_ids == ("one",)


@pytest.mark.anyio
async def test_generation_failure_marks_turn_failed_without_products(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)
    agent = FakeRecommendationAgent(error=ModelProtocolError("vendor secret"))

    with pytest.raises(ModelProtocolError):
        await ConversationService(repository, agent, None).open_turn(
            1, conversation.id, "问题"
        )

    stored = repository.get_conversation(1, conversation.id).turns[0]
    assert stored.status == "failed"
    assert stored.assistant_content is None
    assert stored.recommended_product_ids == ()
    assert stored.error_code == "model_stream_failed"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result",
    [
        RecommendationResult("", ()),
        RecommendationResult("answer", ("same", "same")),
        RecommendationResult("answer", ("",)),
    ],
)
async def test_defensively_rejects_invalid_agent_results(
    tmp_path: Path, result: RecommendationResult
) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)

    with pytest.raises(RecommendationInvalid):
        await ConversationService(
            repository, FakeRecommendationAgent(result), None
        ).open_turn(1, conversation.id, "问题")

    stored = repository.get_conversation(1, conversation.id).turns[0]
    assert stored.status == "failed"
    assert stored.recommended_product_ids == ()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "result",
    [
        RecommendationResult("answer", ("one", "two", "three", "four")),
        RecommendationResult(
            "answer",
            ("one", "two", "three"),
            ("organic", "organic", "organic"),
        ),
        RecommendationResult(
            "answer",
            ("organic", "ad-one", "ad-two"),
            ("organic", "sponsored", "sponsored"),
        ),
        RecommendationResult(
            "answer",
            ("ad", "organic"),
            ("sponsored", "organic"),
        ),
    ],
)
async def test_rejects_bounded_recommendation_violations(
    tmp_path: Path, result: RecommendationResult
) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)

    with pytest.raises(RecommendationInvalid):
        await ConversationService(
            repository, FakeRecommendationAgent(result), None
        ).open_turn(1, conversation.id, "问题")


@pytest.mark.anyio
async def test_early_consumer_close_marks_turn_failed_without_products(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)
    stream = await ConversationService(
        repository,
        FakeRecommendationAgent(RecommendationResult("A" * 100, ("one",))),
        None,
    ).open_turn(1, conversation.id, "问题")
    iterator = stream.__aiter__()
    assert await anext(iterator) == "A" * 64

    await iterator.aclose()

    failed = repository.get_conversation(1, conversation.id).turns[0]
    assert failed.status == "failed"
    assert failed.assistant_content is None
    assert failed.recommended_product_ids == ()


@pytest.mark.anyio
async def test_retry_reuses_failed_turn_and_original_user_content(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    conversation = repository.create_conversation(1)
    failed = repository.create_turn(1, conversation.id, "原问题")
    repository.fail_turn(1, conversation.id, failed.id, "failed")
    agent = FakeRecommendationAgent(RecommendationResult("重试回答", ("one",)))

    stream = await ConversationService(repository, agent, None).retry_turn(
        1, conversation.id, failed.id
    )
    assert [chunk async for chunk in stream] == ["重试回答"]

    turns = repository.get_conversation(1, conversation.id).turns
    assert len(turns) == 1
    assert turns[0].id == failed.id
    assert turns[0].assistant_content == "重试回答"
    assert turns[0].recommended_product_ids == ("one",)
    assert agent.messages[0][-1] == {"role": "user", "content": "原问题"}
    assert "[response_language=zh]" in agent.messages[0][0]["content"]
