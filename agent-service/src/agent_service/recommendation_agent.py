"""OpenAI Agents SDK orchestration for trusted product recommendations."""

from __future__ import annotations

import json
import asyncio
import hashlib
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
from .mall_client import MallOrderRejected, MallServiceUnavailable
from .product_tools import ProductSearchFilters, ProductSearchTool, SearchSort
from .mall_client import OrderData
from .providers import ModelMessage, ModelProtocolError, ModelUpstreamError
from .prompt_loader import load_prompt
from .profile_provider import AgentProfileProvider
from .plan_state import PlanState, validate_plan_state
from .recommendation_scoring import (
    Demand,
    ScoredCandidate,
    SourceSignals,
    apply_sponsored_cutoff,
    parse_demand,
    score_candidates,
)


DEFAULT_RECOMMENDATION_POLICY_PROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "prompts" / "shopping_agent.md"
)
DEFAULT_SEMANTIC_RERANK_PROMPT_PATH = (
    Path(__file__).resolve().parents[2] / "prompts" / "semantic_reranker.md"
)


class ToolArgumentsInvalid(RuntimeError):
    """A search tool call did not match its strict input contract."""


class ToolLoopExceeded(RuntimeError):
    """The model requested more searches than this turn permits."""


class RecommendationInvalid(RuntimeError):
    """The model did not produce a valid, grounded final recommendation."""


class UnknownTool(RuntimeError):
    """Retained for the public error mapping used by the conversation service."""


class SemanticRerankOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    product_ids: Annotated[
        list[Annotated[str, StringConstraints(min_length=1)]], Field(max_length=3)
    ]


class SemanticReranker:
    """Independent Agents SDK action for semantic candidate ordering."""

    def __init__(
        self,
        model: Model,
        prompt_path: str | Path = DEFAULT_SEMANTIC_RERANK_PROMPT_PATH,
    ) -> None:
        self.model = model
        self.prompt_path = Path(prompt_path)

    @staticmethod
    def _fallback_selection(candidates: Sequence[ScoredCandidate]) -> tuple[str, ...]:
        organic = sorted(
            (candidate for candidate in candidates if candidate.channel == "organic"),
            key=lambda candidate: (-candidate.score, candidate.product_id),
        )[:2]
        sponsored = sorted(
            (candidate for candidate in candidates if candidate.channel == "sponsored"),
            key=lambda candidate: (-candidate.score, candidate.product_id),
        )
        selected = organic or sponsored[:1]
        if organic and sponsored:
            selected = [*organic, sponsored[0]]
        return tuple(candidate.product_id for candidate in selected)

    async def rerank(
        self,
        user_request: str,
        candidates: Sequence[ScoredCandidate],
        *,
        environment: dict[str, Any] | None = None,
    ) -> tuple[str, ...]:
        payload = {
            "user_request": user_request,
            "environment": environment or {},
            "candidates": [
                {
                    **asdict(candidate.product),
                    "channel": candidate.channel,
                    "score": round(candidate.score, 3),
                    "score_breakdown": candidate.score_breakdown,
                    "source_evidence": list(candidate.source_evidence),
                    "hard_constraint_status": candidate.hard_constraint_status,
                }
                for candidate in candidates
            ],
        }
        agent = Agent(
            name="niub-semantic-reranker",
            instructions=load_prompt(self.prompt_path),
            model=self.model,
            model_settings=ModelSettings(parallel_tool_calls=False),
        )
        try:
            result = await Runner.run(agent, json.dumps(payload, ensure_ascii=False))
            output = result.final_output
            if not isinstance(output, str):
                raise ValueError("semantic reranker output is not text")
            parsed = SemanticRerankOutput.model_validate(json.loads(output))
            available = {candidate.product_id: candidate for candidate in candidates}
            selected = tuple(dict.fromkeys(parsed.product_ids))
            if any(product_id not in available for product_id in selected):
                raise ValueError("semantic reranker returned an unknown product")
            sources = tuple(available[product_id].channel for product_id in selected)
            if sources.count("organic") > 2 or sources.count("sponsored") > 1:
                raise ValueError("semantic reranker exceeded source limits")
            if sources and sources[0] == "sponsored" and "organic" in sources:
                raise ValueError("semantic reranker placed an ad first")
            return selected
        except Exception:
            return self._fallback_selection(candidates)


@dataclass(frozen=True)
class RecommendationResult:
    answer: str
    product_ids: tuple[str, ...]
    product_sources: tuple[str, ...] = field(default=(), compare=False)
    plan_state: PlanState | None = field(default=None, compare=False)
    plan_version: int = field(default=0, compare=False)
    orders: tuple["OrderReference", ...] = field(default=(), compare=False)


@dataclass(frozen=True)
class OrderReference:
    order_id: str
    preview_path: str
    status: str


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
    conversation_id: str = ""
    turn_id: str = ""
    search_count: int = 0
    searched: bool = False
    found_products: bool = False
    candidate_ids: set[str] = field(default_factory=set)
    candidate_sources: dict[str, str] = field(default_factory=dict)
    purchased_ids: set[str] = field(default_factory=set)
    context_loaded: bool = False
    source_status: dict[str, str] = field(default_factory=dict)
    plan_state: PlanState | None = None
    plan_history: list[PlanState] = field(default_factory=list)
    submission: RecommendationResult | None = None
    orders: dict[str, OrderReference] = field(default_factory=dict)
    search_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    demand: Demand = field(default_factory=Demand)
    user_context: Any | None = None
    agent_profile: Any | None = None
    external_profile: Any | None = None
    organic_products: dict[str, Any] = field(default_factory=dict)
    sponsored_products: dict[str, Any] = field(default_factory=dict)
    scored_organic: list[ScoredCandidate] = field(default_factory=list)
    scored_sponsored: list[ScoredCandidate] = field(default_factory=list)
    user_request: str = ""
    environment: dict[str, Any] = field(default_factory=dict)
    semantic_reranker: SemanticReranker | None = None
    semantic_selection: tuple[str, ...] = ()


def _search_enabled(
    run_context: RunContextWrapper[RecommendationRunContext],
    _agent: Any,
) -> bool:
    return True


def _submit_enabled(
    run_context: RunContextWrapper[RecommendationRunContext],
    _agent: Any,
) -> bool:
    return True


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
                {"items": [], "skipped": "candidates_already_found"},
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
    run_context.context.user_context = context
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
    run_context.context.agent_profile = profile
    run_context.context.source_status["agent_profile"] = (
        "available" if profile.available else "unavailable"
    )
    return json.dumps(asdict(profile), ensure_ascii=False)


async def _update_plan(
    run_context: RunContextWrapper[RecommendationRunContext],
    plan: PlanState,
) -> str:
    run_context.context.plan_state = plan
    run_context.context.plan_history.append(plan)
    return plan.model_dump_json(exclude_none=False)


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
    run_context.context.external_profile = profile
    return json.dumps(asdict(profile), ensure_ascii=False)


def _source_signals(state: RecommendationRunContext) -> SourceSignals:
    context = state.user_context
    browsing: dict[str, dict[str, Any]] = {}
    cart_ids: frozenset[str] = frozenset()
    ordered_ids: frozenset[str] = frozenset()
    if context is not None:
        browsing = {
            item["product_id"]: {
                "count": item.get("count", 1),
                "viewed_at": item.get("viewed_at"),
            }
            for item in context.browsing
            if item.get("product_id")
        }
        cart_ids = frozenset(item["product_id"] for item in context.cart if item.get("product_id"))
        ordered_ids = frozenset(item["product_id"] for item in context.orders if item.get("product_id"))
    return SourceSignals(
        cart_ids=cart_ids,
        browsing=browsing,
        ordered_ids=ordered_ids,
        agent_profile=(state.agent_profile.profile if state.agent_profile else {}),
        external_profile=(state.external_profile.profile if state.external_profile else {}),
    )


def _refresh_scored_candidates(state: RecommendationRunContext) -> None:
    signals = _source_signals(state)
    state.scored_organic = score_candidates(
        list(state.organic_products.values()),
        demand=state.demand,
        signals=signals,
        channel="organic",
        top_k=10,
    )
    state.scored_sponsored = score_candidates(
        list(state.sponsored_products.values()),
        demand=state.demand,
        signals=signals,
        channel="sponsored",
        top_k=3,
    )
    sponsored = state.scored_sponsored
    if state.demand.has_constraints:
        sponsored = apply_sponsored_cutoff(state.scored_organic, sponsored)
    state.candidate_ids = {
        candidate.product_id for candidate in (*state.scored_organic, *sponsored)
    }
    state.candidate_sources = {
        candidate.product_id: candidate.channel
        for candidate in (*state.scored_organic, *sponsored)
    }
    state.scored_sponsored = sponsored


def _scored_payload(state: RecommendationRunContext, source: str) -> dict[str, Any]:
    candidates = state.scored_organic if source == "organic" else state.scored_sponsored
    cutoff = state.scored_organic[-1].score if state.scored_organic else None
    return {
        "source": source,
        "available": True,
        "organic_cutoff_score": cutoff,
        "items": [
            {
                **asdict(candidate.product),
                "score": round(candidate.score, 3),
                "hard_constraint_status": candidate.hard_constraint_status,
                "score_breakdown": candidate.score_breakdown,
                "source_evidence": list(candidate.source_evidence),
            }
            for candidate in candidates
        ],
    }


async def _semantic_rerank_candidates(
    run_context: RunContextWrapper[RecommendationRunContext],
    candidate_ids: list[str] | None = None,
) -> str:
    state = run_context.context
    if state.semantic_reranker is None:
        raise RecommendationInvalid("Semantic reranker is not configured")
    available = {
        candidate.product_id: candidate
        for candidate in (*state.scored_organic, *state.scored_sponsored)
    }
    requested = candidate_ids or list(available)
    candidates = [
        available[product_id] for product_id in requested if product_id in available
    ]
    if not candidates:
        state.semantic_selection = ()
        return json.dumps({"product_ids": []}, ensure_ascii=False)
    environment = {
        **state.environment,
        "source_status": state.source_status,
        "external_profile": (
            asdict(state.external_profile) if state.external_profile is not None else None
        ),
    }
    state.semantic_selection = await state.semantic_reranker.rerank(
        state.user_request,
        candidates,
        environment=environment,
    )
    return json.dumps({"product_ids": list(state.semantic_selection)}, ensure_ascii=False)


async def _get_source_recommendations(
    run_context: RunContextWrapper[RecommendationRunContext],
    *,
    sponsored: bool,
) -> str:
    source = run_context.context.source_tool
    if source is None:
        raise MallServiceUnavailable("Mall service is unavailable")
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
    lane = run_context.context.organic_products if not sponsored else run_context.context.sponsored_products
    lane.update({item.id: item for item in items})
    _refresh_scored_candidates(run_context.context)
    return json.dumps(_scored_payload(run_context.context, result.source), ensure_ascii=False)


async def _get_product_recommendations(run_context: Any) -> str:
    return await _get_source_recommendations(run_context, sponsored=False)


async def _get_ad_recommendations(run_context: Any) -> str:
    return await _get_source_recommendations(run_context, sponsored=True)


class _OrderItem(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    product_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    quantity: Annotated[int, Field(ge=1, le=99)]


async def _create_order_draft(
    run_context: RunContextWrapper[RecommendationRunContext],
    items: list[dict[str, object]],
) -> str:
    source = run_context.context.source_tool
    if source is None or not hasattr(source, "create_order"):
        raise MallServiceUnavailable("Mall service is unavailable")
    try:
        validated = [_OrderItem.model_validate(item) for item in items]
    except ValidationError as error:
        raise ToolArgumentsInvalid("Order arguments are invalid") from error
    normalized = [
        {"product_id": item.product_id, "quantity": item.quantity}
        for item in sorted(validated, key=lambda value: value.product_id)
    ]
    run_context.context.candidate_ids.update(item["product_id"] for item in normalized)
    for item in normalized:
        run_context.context.candidate_sources.setdefault(item["product_id"], "organic")
    intent_hash = hashlib.sha256(
        json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    existing = run_context.context.orders.get(intent_hash)
    if existing is not None:
        return json.dumps(asdict(existing), ensure_ascii=False)
    client_request_id = f"{run_context.context.turn_id or 'unbound'}:{intent_hash}"
    try:
        order: OrderData = await source.create_order(normalized, client_request_id)
    except MallOrderRejected as error:
        raise ToolArgumentsInvalid(f"Order was rejected: {error}") from error
    except MallServiceUnavailable:
        raise
    reference = OrderReference(
        order_id=order.id,
        preview_path=f"/orders/{order.id}/preview",
        status=order.status,
    )
    run_context.context.orders[intent_hash] = reference
    return json.dumps(asdict(reference), ensure_ascii=False)


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
    if state.semantic_selection:
        deduplicated_ids = state.semantic_selection
    if any(product_id not in state.candidate_ids for product_id in deduplicated_ids):
        raise RecommendationInvalid("Recommendation response is invalid")
    product_sources = tuple(
        state.candidate_sources.get(product_id, "organic")
        for product_id in deduplicated_ids
    )
    sponsored_count = product_sources.count("sponsored")
    organic_count = product_sources.count("organic")
    if sponsored_count > 1 or organic_count > 2:
        raise RecommendationInvalid("Recommendation response is invalid")
    if sponsored_count and organic_count and product_sources[0] == "sponsored":
        raise RecommendationInvalid("Recommendation response is invalid")
    if state.semantic_selection and deduplicated_ids != state.semantic_selection:
        raise RecommendationInvalid("Recommendation must use semantic rerank selection")
    state.submission = RecommendationResult(
        answer=submission.answer,
        product_ids=deduplicated_ids,
        product_sources=product_sources,
        orders=tuple(state.orders.values()),
    )
    return json.dumps(
        {
            "answer": submission.answer,
            "product_ids": list(deduplicated_ids),
            "product_sources": list(product_sources),
            "orders": [asdict(order) for order in state.orders.values()],
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

_SEMANTIC_RERANK_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "candidate_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "maxItems": 13,
        }
    },
    "additionalProperties": False,
}

_PLAN_STATE_SCHEMA: dict[str, object] = PlanState.model_json_schema()

_CREATE_ORDER_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "minItems": 1,
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "minLength": 1},
                    "quantity": {"type": "integer", "minimum": 1, "maximum": 99},
                },
                "required": ["product_id", "quantity"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
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


async def _invoke_update_plan_tool(run_context: Any, arguments: str) -> str:
    try:
        payload = json.loads(arguments)
        if not isinstance(payload, dict):
            raise TypeError
        plan = validate_plan_state(payload)
        return await _execute_with_timeout(run_context, _update_plan(run_context, plan))
    except (json.JSONDecodeError, TypeError, ValidationError) as error:
        raise RecommendationInvalid("Plan state is invalid") from error


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


async def _invoke_semantic_rerank_tool(run_context: Any, arguments: str) -> str:
    try:
        payload = json.loads(arguments)
        if not isinstance(payload, dict):
            raise TypeError
        candidate_ids = payload.get("candidate_ids")
        if candidate_ids is not None and not isinstance(candidate_ids, list):
            raise TypeError
        return await _execute_with_timeout(
            run_context,
            _semantic_rerank_candidates(run_context, candidate_ids),
        )
    except (json.JSONDecodeError, TypeError) as error:
        raise RecommendationInvalid("Semantic rerank arguments are invalid") from error


async def _invoke_create_order_tool(run_context: Any, arguments: str) -> str:
    try:
        payload = json.loads(arguments)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise TypeError
        return await _execute_with_timeout(
            run_context, _create_order_draft(run_context, payload["items"])
        )
    except (json.JSONDecodeError, TypeError) as error:
        raise ToolArgumentsInvalid("Order arguments are invalid") from error


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
        semantic_rerank_prompt_path: str | Path = DEFAULT_SEMANTIC_RERANK_PROMPT_PATH,
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
        self._semantic_reranker = SemanticReranker(model, semantic_rerank_prompt_path)
        self._conversation_id = ""
        self._turn_id = ""
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
        self._tools.insert(
            1,
            FunctionTool(
                name="semantic_rerank_recommendations",
                description="Use the independent semantic reranker to select and order final candidates.",
                params_json_schema=_SEMANTIC_RERANK_SCHEMA,
                on_invoke_tool=_invoke_semantic_rerank_tool,
                strict_json_schema=True,
            ),
        )
        if self._source_tool is not None:
            if hasattr(self._source_tool, "create_order"):
                self._tools.append(
                    FunctionTool(
                        name="create_order_draft",
                        description="Create a pending-payment order only after the user explicitly confirms purchase.",
                        params_json_schema=_CREATE_ORDER_SCHEMA,
                        on_invoke_tool=_invoke_create_order_tool,
                        strict_json_schema=True,
                    )
                )
            self._tools[0:0] = [
                FunctionTool(
                    name="update_plan",
                    description="Create or update the model-owned execution plan.",
                    params_json_schema=_PLAN_STATE_SCHEMA,
                    on_invoke_tool=_invoke_update_plan_tool,
                    strict_json_schema=True,
                ),
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
            conversation_id=self._conversation_id,
            turn_id=self._turn_id,
            semantic_reranker=self._semantic_reranker,
        )
        try:
            last_user_message = next(
                (
                    message["content"]
                    for message in reversed(messages)
                    if message["role"] == "user"
                ),
                "",
            )
            context.demand = parse_demand(last_user_message)
            context.user_request = last_user_message
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
            if context.orders:
                order_text = "\n".join(
                    f"- {order.order_id}: {order.preview_path}"
                    for order in context.orders.values()
                )
                return RecommendationResult(
                    answer=(
                        "订单已创建，状态为待支付。请打开订单预览页完成支付。 / "
                        "The order was created and is pending payment. Open the order preview to pay.\n"
                        f"{order_text}"
                    ),
                    product_ids=(),
                    orders=tuple(context.orders.values()),
                )
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
            return RecommendationResult(
                answer=context.submission.answer,
                product_ids=context.submission.product_ids,
                product_sources=context.submission.product_sources,
                plan_state=context.plan_state,
                plan_version=len(context.plan_history),
                orders=context.submission.orders,
            )
        if context.searched:
            raise RecommendationInvalid("Recommendation response is invalid")
        answer = result.final_output
        if not isinstance(answer, str):
            raise RecommendationInvalid("Recommendation response is invalid")
        answer = answer.strip()
        if not answer or len(answer) > 20000:
            raise RecommendationInvalid("Recommendation response is invalid")
        return RecommendationResult(
            answer=answer,
            product_ids=(),
            plan_state=context.plan_state,
            plan_version=len(context.plan_history),
            orders=tuple(context.orders.values()),
        )

    def set_turn_context(self, conversation_id: str, turn_id: str) -> None:
        self._conversation_id = conversation_id
        self._turn_id = turn_id


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
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        max_turns=config.max_turns,
        tool_timeout_seconds=config.tool_timeout_seconds,
    )
