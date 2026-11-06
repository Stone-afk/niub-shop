import pytest
from pydantic import ValidationError

from agent_service.plan_state import PlanState, validate_plan_state


def valid_state() -> dict[str, object]:
    return {
        "plan_id": "plan-1",
        "goal": "推荐商品",
        "status": "in_progress",
        "current_step_id": "profile",
        "steps": [
            {
                "id": "profile",
                "title": "加载画像",
                "status": "in_progress",
                "depends_on": [],
            },
            {
                "id": "recommend",
                "title": "查询推荐",
                "status": "pending",
                "depends_on": ["profile"],
            },
        ],
        "next_action": "get_agent_profile",
        "reason": "先加载用户画像",
    }


def test_validates_plan_state_and_step_references() -> None:
    plan = validate_plan_state(valid_state())

    assert isinstance(plan, PlanState)
    assert plan.plan_id == "plan-1"
    assert plan.steps[1].depends_on == ["profile"]


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload["steps"].append(payload["steps"][0].copy()),
        lambda payload: payload.update(current_step_id="missing"),
        lambda payload: payload.update(goal=" "),
        lambda payload: payload["steps"][1].update(depends_on=["missing"]),
        lambda payload: payload.update(status="unknown"),
    ],
)
def test_rejects_invalid_plan_state(mutator) -> None:
    payload = valid_state()
    mutator(payload)

    with pytest.raises(ValidationError):
        validate_plan_state(payload)


def test_rejects_more_than_64_steps() -> None:
    payload = valid_state()
    payload["steps"] = [
        {
            "id": f"step-{index}",
            "title": "步骤",
            "status": "pending",
            "depends_on": [],
        }
        for index in range(65)
    ]

    with pytest.raises(ValidationError):
        validate_plan_state(payload)
