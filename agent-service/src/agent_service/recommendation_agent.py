"""OpenAI Agents SDK orchestration for trusted product recommendations."""

from __future__ import annotations

import json
import asyncio
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Annotated, Any, cast

from agents import (
    Agent,
    FunctionTool,
    Model,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunConfig,
    RunContextWrapper,
    Runner,
)
from agents.run_config import ToolExecutionConfig
from agents.exceptions import MaxTurnsExceeded, ModelBehaviorError, UserError
from openai import APIError, AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from .config import LLMConfig
from .mall_client import MallServiceUnavailable
from .product_tools import ProductSearchFilters, ProductSearchTool, SearchSort
from .providers import ModelMessage, ModelProtocolError, ModelUpstreamError
from .prompt_loader import load_prompt
from .profile_provider import AgentProfileProvider


DEFAULT_RECOMMENDATION_POLICY_PROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "prompts" / "recommendation-policy.md"
)


class ToolArgumentsInvalid(RuntimeError):
    """A search tool call did not match its strict input contract."""


class ToolLoopExceeded(RuntimeError):
    """The model requested more searches than this turn permits."""


class RecommendationInvalid(RuntimeError):
    """The model did not produce a valid, grounded final recommendation."""


class UnknownTool(RuntimeError):
    """Retained for the public error mapping used by the conversation service."""


@dataclass(frozen=True)
class RecommendationResult:
    answer: str
    product_ids: tuple[str, ...]
    product_sources: tuple[str, ...] = field(default=(), compare=False)


class _RecommendationSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    answer: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20000)
    ]
    product_ids: Annotated[
        list[Annotated[str, StringConstraints(min_length=1)]], Field(max_length=3)
    ]


@dataclass
class RecommendationRunContext:
    """Mutable state owned by one SDK Runner invocation."""

    product_tool: ProductSearchTool
    max_search_calls: int
    source_tool: Any | None = None
    profile_provider: AgentProfileProvider | None = None
    max_turns: int = 10
    tool_timeout_seconds: float = 1800.0
    user_id: int = 1
    search_count: int = 0
    searched: bool = False
    found_products: bool = False
    candidate_ids: set[str] = field(default_factory=set)
    candidate_sources: dict[str, str] = field(default_factory=dict)
    purchased_ids: set[str] = field(default_factory=set)
    context_loaded: bool = False
    source_status: dict[str, str] = field(default_factory=dict)
    submission: RecommendationResult | None = None
    search_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def _search_enabled(
    run_context: RunContextWrapper[RecommendationRunContext],
    _agent: Any,
) -> bool:
    return not run_context.context.found_products


def _submit_enabled(
    run_context: RunContextWrapper[RecommendationRunContext],
    _agent: Any,
) -> bool:
    return run_context.context.searched


async def _search_products(
    run_context: RunContextWrapper[RecommendationRunContext],
    keyword: str | None = None,
    category: str | None = None,
    min_price_cents: int | None = None,
    max_price_cents: int | None = None,
    sort: SearchSort = "relevance",
    limit: int = 5,
) -> str:
    """Search the trusted mall catalog using structured filters."""

    state = run_context.context
    async with state.search_lock:
        if state.found_products:
            return json.dumps(
                {
                    "items": [],
                    "note": "Products were already found. Submit the recommendation now.",
                },
                ensure_ascii=False,
            )
        if state.search_count >= state.max_search_calls:
            raise ToolLoopExceeded("Product search limit exceeded")
        try:
            filters = ProductSearchFilters.model_validate(
                {
                    "keyword": keyword,
                    "category": category,
                    "min_price_cents": min_price_cents,
                    "max_price_cents": max_price_cents,
                    "sort": sort,
                    "limit": limit,
                }
            )
        except ValidationError as error:
            raise ToolArgumentsInvalid("Product search arguments are invalid") from error

        state.search_count += 1
        state.searched = True
        try:
            products = await state.product_tool.search(filters)
        except MallServiceUnavailable as error:
            raise MallServiceUnavailable("Mall service is unavailable") from error

        if state.source_tool is not None and not state.context_loaded:
            await _get_user_context(run_context)
        if state.purchased_ids:
            products = tuple(
                product for product in products if product.id not in state.purchased_ids
            )

        state.candidate_ids.update(product.id for product in products)
        for product in products:
            state.candidate_sources.setdefault(product.id, "organic")
        state.found_products = state.found_products or bool(products)
    return json.dumps(
        {"items": [asdict(product) for product in products]},
        ensure_ascii=False,
    )


async def _get_user_context(run_context: RunContextWrapper[RecommendationRunContext]) -> str:
    source = run_context.context.source_tool
    if source is None:
        run_context.context.context_loaded = True
        return json.dumps(
            {
                "available": False,
                "error": "not_configured",
                "browsing": [],
                "orders": [],
                "cart": [],
            },
            ensure_ascii=False,
        )
    try:
        context = await source.get_user_context(run_context.context.user_id)
    except MallServiceUnavailable:
        run_context.context.context_loaded = True
        run_context.context.source_status["user_context"] = "unavailable"
        return json.dumps(
            {
                "available": False,
                "error": "source_unavailable",
                "browsing": [],
                "orders": [],
                "cart": [],
            },
            ensure_ascii=False,
        )
    run_context.context.purchased_ids.update(context.purchased_product_ids)
    run_context.context.context_loaded = True
    run_context.context.source_status["user_context"] = (
        "available" if context.available else "unavailable"
    )
    return json.dumps(asdict(context), ensure_ascii=False)


async def _get_agent_profile(
    run_context: RunContextWrapper[RecommendationRunContext],
) -> str:
    provider = run_context.context.profile_provider
    if provider is None:
        return json.dumps(
            {"source": "agent", "available": False, "error": "not_configured"},
            ensure_ascii=False,
        )
    profile = provider.get_profile(run_context.context.user_id)
    run_context.context.source_status["agent_profile"] = (
        "available" if profile.available else "unavailable"
    )
    return json.dumps(asdict(profile), ensure_ascii=False)


async def _get_external_profile(
    run_context: RunContextWrapper[RecommendationRunContext],
) -> str:
    source = run_context.context.source_tool
    if source is None or not hasattr(source, "get_external_profile"):
        return json.dumps(
            {
                "source": "recommendation-platform",
                "available": False,
                "error": "not_configured",
            },
            ensure_ascii=False,
        )
    try:
        profile = await source.get_external_profile(run_context.context.user_id)
    except MallServiceUnavailable:
        run_context.context.source_status["external_profile"] = "unavailable"
        return json.dumps(
            {
                "source": "recommendation-platform",
                "available": False,
                "error": "source_unavailable",
            },
            ensure_ascii=False,
        )
    run_context.context.source_status["external_profile"] = (
        "available" if profile.available else "unavailable"
    )
    return json.dumps(asdict(profile), ensure_ascii=False)


async def _get_source_recommendations(
    run_context: RunContextWrapper[RecommendationRunContext],
    *,
    sponsored: bool,
) -> str:
    source = run_context.context.source_tool
    if source is None:
        raise MallServiceUnavailable("Mall service is unavailable")
    if not run_context.context.context_loaded:
        await _get_user_context(run_context)
    try:
        result = (
            await source.get_ad_recommendations(run_context.context.user_id)
            if sponsored
            else await source.get_product_recommendations(run_context.context.user_id)
        )
    except MallServiceUnavailable:
        source_name = "sponsored" if sponsored else "organic"
        run_context.context.searched = True
        run_context.context.source_status[f"{source_name}_recommendations"] = "unavailable"
        return json.dumps(
            {"source": source_name, "available": False, "items": []},
            ensure_ascii=False,
        )
    run_context.context.searched = True
    items = [item for item in result.items if item.id not in run_context.context.purchased_ids]
    for item in items:
        run_context.context.candidate_ids.add(item.id)
        run_context.context.candidate_sources[item.id] = result.source
    return json.dumps(
        {
            "source": result.source,
            "available": True,
            "items": [asdict(item) for item in items],
        },
        ensure_ascii=False,
    )


async def _get_product_recommendations(run_context: Any) -> str:
    return await _get_source_recommendations(run_context, sponsored=False)


async def _get_ad_recommendations(run_context: Any) -> str:
    return await _get_source_recommendations(run_context, sponsored=True)


async def _submit_product_recommendation(
    run_context: RunContextWrapper[RecommendationRunContext],
    answer: str,
    product_ids: list[str],
) -> str:
    """Submit the final answer and product IDs returned by this turn's searches."""

    state = run_context.context
    # Some OpenAI-compatible providers serialize newlines as a literal ``\\n``
    # sequence in tool arguments. Normalize that transport artifact before it
    # is persisted and streamed to the Web client.
    answer = (
        answer.replace("\\\\r\\\\n", "\n")
        .replace("\\\\n", "\n")
        .replace("\\r\\n", "\n")
        .replace("\\n", "\n")
    )
    try:
        submission = _RecommendationSubmission.model_validate(
            {"answer": answer, "product_ids": product_ids}
        )
    except ValidationError as error:
        raise RecommendationInvalid("Recommendation response is invalid") from error

    deduplicated_ids = tuple(dict.fromkeys(submission.product_ids))
    if any(product_id not in state.candidate_ids for product_id in deduplicated_ids):
        raise RecommendationInvalid("Recommendation response is invalid")
    product_sources = tuple(
        state.candidate_sources.get(product_id, "organic")
        for product_id in deduplicated_ids
    )
    sponsored_count = product_sources.count("sponsored")
    if sponsored_count > 1:
        raise RecommendationInvalid("Recommendation response is invalid")
    state.submission = RecommendationResult(
        answer=submission.answer,
        product_ids=deduplicated_ids,
        product_sources=product_sources,
    )
    return json.dumps(
        {
            "answer": submission.answer,
            "product_ids": list(deduplicated_ids),
            "product_sources": list(product_sources),
        },
        ensure_ascii=False,
    )


_SEARCH_PRODUCTS_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "keyword": {"type": "string", "maxLength": 200},
        "category": {"type": "string", "maxLength": 100},
        "min_price_cents": {"type": "integer", "minimum": 0},
        "max_price_cents": {"type": "integer", "minimum": 0},
        "sort": {
            "type": "string",
            "enum": ["relevance", "price_asc", "price_desc"],
            "default": "relevance",
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
    },
    "additionalProperties": False,
}

_SUBMIT_RECOMMENDATION_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string", "minLength": 1, "maxLength": 20000},
        "product_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 3,
        },
    },
    "required": ["answer", "product_ids"],
    "additionalProperties": False,
}


async def _execute_with_timeout(
    run_context: RunContextWrapper[RecommendationRunContext],
    operation: Any,
) -> str:
    try:
        return await asyncio.wait_for(
            operation,
            timeout=run_context.context.tool_timeout_seconds,
        )
    except TimeoutError as error:
        raise ToolLoopExceeded("Tool execution timed out") from error


async def _invoke_search_tool(run_context: Any, arguments: str) -> str:
    try:
        payload = json.loads(arguments)
        if not isinstance(payload, dict):
            raise TypeError
        return await _execute_with_timeout(
            run_context, _search_products(run_context, **payload)
        )
    except (json.JSONDecodeError, TypeError) as error:
        raise ToolArgumentsInvalid("Product search arguments are invalid") from error


async def _invoke_submit_tool(run_context: Any, arguments: str) -> str:
    try:
        payload = json.loads(arguments)
        if not isinstance(payload, dict):
            raise TypeError
        return await _execute_with_timeout(
            run_context, _submit_product_recommendation(run_context, **payload)
        )
    except (json.JSONDecodeError, TypeError) as error:
        raise RecommendationInvalid("Recommendation response is invalid") from error


class RecommendationAgent:
    """Thin domain adapter around the SDK-owned ReAct loop."""

    def __init__(
        self,
        model: Model,
        product_tool: ProductSearchTool,
        *,
        max_search_calls: int = 3,
        model_name: str | None = None,
        recommendation_policy_prompt_path: str | Path = (
            DEFAULT_RECOMMENDATION_POLICY_PROMPT_PATH
        ),
        user_id: int = 1,
        profile_provider: AgentProfileProvider | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_turns: int = 10,
        tool_timeout_seconds: float = 1800.0,
    ) -> None:
        if max_search_calls < 0:
            raise ValueError("max_search_calls must be non-negative")
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        if tool_timeout_seconds <= 0:
            raise ValueError("tool_timeout_seconds must be positive")
        self._product_tool = product_tool
        self._source_tool = product_tool if all(
            hasattr(product_tool, name)
            for name in (
                "get_user_context",
                "get_product_recommendations",
                "get_ad_recommendations",
            )
        ) else None
        self._user_id = user_id
        self._profile_provider = profile_provider or AgentProfileProvider()
        self._max_search_calls = max_search_calls
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._max_turns = max_turns
        self._tool_timeout_seconds = tool_timeout_seconds
        self.model_name = model_name or str(
            getattr(model, "model", type(model).__name__)
        )
        self._model = model
        self._recommendation_policy_prompt_path = Path(
            recommendation_policy_prompt_path
        )
        self.tracing_disabled = True
        search_tool = FunctionTool(
            name="search_products",
            description="Search the trusted mall catalog using structured filters.",
            params_json_schema=_SEARCH_PRODUCTS_SCHEMA,
            on_invoke_tool=_invoke_search_tool,
            strict_json_schema=True,
            is_enabled=_search_enabled,
        )
        submit_tool = FunctionTool(
            name="submit_product_recommendation",
            description="Submit the final answer and verified product IDs.",
            params_json_schema=_SUBMIT_RECOMMENDATION_SCHEMA,
            on_invoke_tool=_invoke_submit_tool,
            strict_json_schema=True,
            is_enabled=_submit_enabled,
        )
        self._tools = [search_tool, submit_tool]
        if self._source_tool is not None:
            self._tools[0:0] = [
                FunctionTool(
                    name="get_agent_profile",
                    description="Load the user profile maintained by the Agent service.",
                    params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
                    on_invoke_tool=lambda context, _arguments: _execute_with_timeout(
                        context, _get_agent_profile(context)
                    ),
                    strict_json_schema=True,
                ),
                FunctionTool(
                    name="get_external_profile",
                    description="Load the profile from the external recommendation platform.",
                    params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
                    on_invoke_tool=lambda context, _arguments: _execute_with_timeout(
                        context, _get_external_profile(context)
                    ),
                    strict_json_schema=True,
                ),
                FunctionTool(
                    name="get_user_context",
                    description="Load browsing history, orders, cart, and user profile.",
                    params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
                    on_invoke_tool=lambda context, _arguments: _execute_with_timeout(
                        context, _get_user_context(context)
                    ),
                    strict_json_schema=True,
                ),
                FunctionTool(
                    name="get_product_recommendations",
                    description="Load organic product recommendations from the mall.",
                    params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
                    on_invoke_tool=lambda context, _arguments: _execute_with_timeout(
                        context, _get_product_recommendations(context)
                    ),
                    strict_json_schema=True,
                ),
                FunctionTool(
                    name="get_ad_recommendations",
                    description="Load sponsored product recommendations from the ad system.",
                    params_json_schema={"type": "object", "properties": {}, "additionalProperties": False},
                    on_invoke_tool=lambda context, _arguments: _execute_with_timeout(
                        context, _get_ad_recommendations(context)
                    ),
                    strict_json_schema=True,
                ),
            ]
        self._run_config = RunConfig(
            tracing_disabled=True,
            trace_include_sensitive_data=False,
            workflow_name="niub-shopping-recommendation",
            tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
        )

    async def generate(
        self, messages: Sequence[ModelMessage]
    ) -> RecommendationResult:
        agent: Agent[RecommendationRunContext] = Agent(
            name="niub-shopping-agent",
            instructions=load_prompt(self._recommendation_policy_prompt_path),
            model=self._model,
            tools=self._tools,
            model_settings=ModelSettings(
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                parallel_tool_calls=False,
            ),
            tool_use_behavior={
                "stop_at_tool_names": ["submit_product_recommendation"]
            },
        )
        context = RecommendationRunContext(
            product_tool=self._product_tool,
            max_search_calls=self._max_search_calls,
            source_tool=self._source_tool,
            profile_provider=self._profile_provider,
            max_turns=self._max_turns,
            tool_timeout_seconds=self._tool_timeout_seconds,
            user_id=self._user_id,
        )
        try:
            result = await Runner.run(
                agent,
                cast(Any, list(messages)),
                context=context,
                max_turns=self._max_turns,
                run_config=self._run_config,
            )
        except APIError as error:
            raise ModelUpstreamError("Model service is unavailable") from error
        except json.JSONDecodeError as error:
            raise ModelProtocolError("Model response was not valid JSON") from error
        except ValidationError as error:
            raise ModelProtocolError("Model response was invalid") from error
        except (AttributeError, TypeError) as error:
            raise ModelProtocolError("Model response was invalid") from error
        except MaxTurnsExceeded as error:
            raise ToolLoopExceeded("Recommendation tool loop exceeded") from error
        except (ModelBehaviorError, UserError) as error:
            cause = error.__cause__
            if isinstance(
                cause,
                (MallServiceUnavailable, ToolArgumentsInvalid, ToolLoopExceeded),
            ):
                raise cause
            if isinstance(cause, RecommendationInvalid):
                raise cause
            raise RecommendationInvalid("Recommendation response is invalid") from error

        if context.submission is not None:
            return context.submission
        if context.searched:
            raise RecommendationInvalid("Recommendation response is invalid")
        answer = result.final_output
        if not isinstance(answer, str):
            raise RecommendationInvalid("Recommendation response is invalid")
        answer = answer.strip()
        if not answer or len(answer) > 20000:
            raise RecommendationInvalid("Recommendation response is invalid")
        return RecommendationResult(answer=answer, product_ids=())


def create_recommendation_agent(
    config: LLMConfig,
    product_tool: ProductSearchTool,
    *,
    client_factory: Callable[..., Any] = AsyncOpenAI,
) -> RecommendationAgent:
    """Build an SDK agent against an OpenAI-compatible Chat Completions endpoint."""

    client = client_factory(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout_seconds,
    )
    model = OpenAIChatCompletionsModel(
        model=config.model,
        openai_client=cast(AsyncOpenAI, client),
    )
    return RecommendationAgent(
        model,
        product_tool,
        model_name=config.model,
        recommendation_policy_prompt_path=config.recommendation_policy_prompt_file,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        max_turns=config.max_turns,
        tool_timeout_seconds=config.tool_timeout_seconds,
    )
