"""Deterministic, explainable candidate scoring for recommendation lanes."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from .mall_client import ProductData


Channel = Literal["organic", "sponsored"]


@dataclass(frozen=True)
class Demand:
    category: str | None = None
    keyword: str | None = None
    min_price_cents: int | None = None
    max_price_cents: int | None = None
    min_capacity_ml: int | None = None
    max_capacity_ml: int | None = None
    required_tags: frozenset[str] = frozenset()
    excluded_tags: frozenset[str] = frozenset()

    @property
    def has_constraints(self) -> bool:
        return any(
            (
                self.category,
                self.keyword,
                self.min_price_cents is not None,
                self.max_price_cents is not None,
                self.min_capacity_ml is not None,
                self.max_capacity_ml is not None,
                self.required_tags,
                self.excluded_tags,
            )
        )


@dataclass(frozen=True)
class SourceSignals:
    cart_ids: frozenset[str] = frozenset()
    browsing: dict[str, dict[str, Any]] = field(default_factory=dict)
    ordered_ids: frozenset[str] = frozenset()
    agent_profile: dict[str, Any] = field(default_factory=dict)
    external_profile: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScoredCandidate:
    product: ProductData
    channel: Channel
    score: float
    hard_constraint_status: Literal["matched", "unknown"]
    score_breakdown: dict[str, float]
    source_evidence: tuple[str, ...]

    @property
    def product_id(self) -> str:
        return self.product.id


_PRICE_RE = re.compile(r"(?P<value>\d+(?:\.\d+)?)\s*(?:元|块|twd|nt\$)?", re.I)
_CAPACITY_RE = re.compile(r"(?P<low>\d+)\s*(?:到|至|-|–|~)\s*(?P<high>\d+)\s*ml", re.I)


def parse_demand(text: str) -> Demand:
    """Extract conservative hard constraints from a natural-language request."""

    lowered = text.casefold()
    category = "drinkware" if any(token in lowered for token in ("杯", "mug", "cup", "tumbler")) else None
    capacity = _CAPACITY_RE.search(lowered)
    max_price = None
    budget_match = re.search(r"(?:预算|以内|under|below|budget)[^\d]{0,12}" + _PRICE_RE.pattern, lowered)
    if budget_match:
        max_price = int(float(budget_match.group("value")) * 100)
    required_tags = set()
    for token, tag in (("咖啡", "coffee"), ("coffee", "coffee"), ("茶", "tea"), ("tea", "tea"), ("办公室", "office"), ("office", "office"), ("带盖", "covered"), ("lid", "covered")):
        if token in lowered:
            required_tags.add(tag)
    return Demand(
        category=category,
        keyword=text.strip() or None,
        max_price_cents=max_price,
        min_capacity_ml=int(capacity.group("low")) if capacity else None,
        max_capacity_ml=int(capacity.group("high")) if capacity else None,
        required_tags=frozenset(required_tags),
    )


def score_candidates(
    products: list[ProductData] | tuple[ProductData, ...],
    *,
    demand: Demand,
    signals: SourceSignals,
    channel: Channel,
    now: datetime | None = None,
    top_k: int | None = None,
) -> list[ScoredCandidate]:
    now = now or datetime.now(timezone.utc)
    scored: list[ScoredCandidate] = []
    for product in products:
        if not _matches_hard_constraints(product, demand):
            continue
        breakdown: dict[str, float] = {
            "demand": _demand_score(product, demand),
            "cart": 20.0 if product.id in signals.cart_ids else 0.0,
            "browsing": _browsing_score(product.id, signals.browsing, now),
            "orders": 3.0 if product.id in signals.ordered_ids else 0.0,
            "agent_profile": _profile_score(product, signals.agent_profile, 8.0),
            "external_profile": _profile_score(product, signals.external_profile, 5.0),
            "organic_baseline": 2.0 if channel == "organic" else 0.0,
            "sponsored_bonus": 0.0,
            "repeat_penalty": -8.0 if product.id in signals.ordered_ids else 0.0,
        }
        score = max(0.0, min(100.0, sum(breakdown.values())))
        evidence = tuple(
            key
            for key in ("cart", "browsing", "orders", "agent_profile", "external_profile")
            if breakdown[key] > 0
        )
        scored.append(
            ScoredCandidate(product, channel, score, "matched", breakdown, evidence)
        )
    scored.sort(key=lambda item: (-item.score, item.product.id))
    return scored[:top_k] if top_k is not None else scored


def apply_sponsored_cutoff(
    organic_top10: list[ScoredCandidate],
    sponsored_top3: list[ScoredCandidate],
) -> list[ScoredCandidate]:
    """Keep ads only when they clear the organic Top 10 minimum score."""

    if not organic_top10:
        return sponsored_top3[:1]
    cutoff = organic_top10[-1].score
    return [candidate for candidate in sponsored_top3 if candidate.score > cutoff]


def _matches_hard_constraints(product: ProductData, demand: Demand) -> bool:
    if product.availability != "in_stock":
        return False
    if demand.category and product.category.casefold() != demand.category.casefold():
        return False
    if demand.max_price_cents is not None and product.price_cents > demand.max_price_cents:
        return False
    if demand.min_price_cents is not None and product.price_cents < demand.min_price_cents:
        return False
    if demand.min_capacity_ml is not None and (product.capacity_ml is None or product.capacity_ml < demand.min_capacity_ml):
        return False
    if demand.max_capacity_ml is not None and (product.capacity_ml is None or product.capacity_ml > demand.max_capacity_ml):
        return False
    tags = {tag.casefold() for tag in product.tags}
    if demand.required_tags and not demand.required_tags.issubset(tags):
        return False
    if demand.excluded_tags.intersection(tags):
        return False
    return True


def _demand_score(product: ProductData, demand: Demand) -> float:
    if not demand.has_constraints:
        return 0.0
    score = 0.0
    tags = {tag.casefold() for tag in product.tags}
    if demand.category and product.category.casefold() == demand.category.casefold():
        score += 10.0
    if demand.required_tags:
        score += 10.0 * len(demand.required_tags.intersection(tags)) / len(demand.required_tags)
    if demand.max_price_cents is not None or demand.min_price_cents is not None:
        score += 6.0
    if demand.min_capacity_ml is not None or demand.max_capacity_ml is not None:
        score += 6.0
    if demand.keyword:
        haystack = " ".join((product.name, product.description, *product.tags)).casefold()
        if any(token and token.casefold() in haystack for token in demand.keyword.split()):
            score += 8.0
    return min(40.0, score)


def _browsing_score(product_id: str, browsing: dict[str, dict[str, Any]], now: datetime) -> float:
    record = browsing.get(product_id)
    if not record:
        return 0.0
    count = max(1, int(record.get("count", 1)))
    strength = min(1.0, math.log1p(count) / math.log1p(6))
    try:
        viewed_at = datetime.fromisoformat(str(record["viewed_at"]).replace("Z", "+00:00"))
        age_days = max(0.0, (now - viewed_at).total_seconds() / 86400)
    except (KeyError, TypeError, ValueError):
        age_days = 30.0
    decay = math.exp(-math.log(2) * age_days / 14.0)
    return 15.0 * strength * decay


def _profile_score(product: ProductData, profile: dict[str, Any], maximum: float) -> float:
    if not profile:
        return 0.0
    categories = {str(value).casefold() for value in profile.get("preferred_categories", profile.get("interest_categories", []))}
    tags = {str(value).casefold() for value in profile.get("preferred_tags", profile.get("interest_tags", []))}
    category_match = product.category.casefold() in categories
    tag_match = bool(tags.intersection(tag.casefold() for tag in product.tags))
    if category_match and tag_match:
        return maximum
    if category_match or tag_match:
        return maximum / 2
    return 0.0
