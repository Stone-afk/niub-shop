"""Structured product search backed by the mall catalog."""

from typing import Annotated, Literal, Protocol

import anyio
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from .mall_client import MallClient, ProductData


SearchSort = Literal["relevance", "price_asc", "price_desc"]


class ProductSearchFilters(BaseModel):
    """Strict, bounded filters accepted from a model tool call."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    keyword: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=200)
    ] | None = None
    category: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=100)
    ] | None = None
    min_price_cents: Annotated[int, Field(ge=0)] | None = None
    max_price_cents: Annotated[int, Field(ge=0)] | None = None
    sort: SearchSort = "relevance"
    limit: Annotated[int, Field(ge=1, le=10)] = 5

    @field_validator("keyword", "category", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def validate_price_range(self) -> "ProductSearchFilters":
        if (
            self.min_price_cents is not None
            and self.max_price_cents is not None
            and self.min_price_cents > self.max_price_cents
        ):
            raise ValueError("minimum price cannot exceed maximum price")
        return self


class ProductSearchTool(Protocol):
    async def search(
        self, filters: ProductSearchFilters
    ) -> tuple[ProductData, ...]: ...


class MallProductSearchTool:
    def __init__(self, client: MallClient) -> None:
        self._client = client

    async def search(
        self, filters: ProductSearchFilters
    ) -> tuple[ProductData, ...]:
        products = await anyio.to_thread.run_sync(self._client.list_products)
        matches: list[tuple[int, int, ProductData]] = []
        keyword = _normalize_keyword(filters.keyword) if filters.keyword else None
        category = filters.category.casefold() if filters.category else None

        for position, product in enumerate(products):
            if product.availability != "in_stock":
                continue
            if category is not None and product.category.casefold() != category:
                continue
            if (
                filters.min_price_cents is not None
                and product.price_cents < filters.min_price_cents
            ):
                continue
            if (
                filters.max_price_cents is not None
                and product.price_cents > filters.max_price_cents
            ):
                continue

            score = _relevance_score(product, keyword) if keyword else 0
            if keyword and score == 0:
                continue
            matches.append((position, score, product))

        if filters.sort == "relevance":
            matches.sort(key=lambda item: (-item[1], item[0]))
        elif filters.sort == "price_asc":
            matches.sort(key=lambda item: (item[2].price_cents, item[0]))
        else:
            matches.sort(key=lambda item: (-item[2].price_cents, item[0]))

        return tuple(item[2] for item in matches[: filters.limit])

    async def get_user_context(self, user_id: int):
        return await anyio.to_thread.run_sync(self._client.get_user_context, user_id)

    async def get_external_profile(self, user_id: int):
        return await anyio.to_thread.run_sync(self._client.get_external_profile, user_id)

    async def get_product_recommendations(self, user_id: int):
        return await anyio.to_thread.run_sync(self._client.get_recommendations, user_id)

    async def get_ad_recommendations(self, user_id: int):
        return await anyio.to_thread.run_sync(
            self._client.get_ad_recommendations, user_id
        )


def _relevance_score(product: ProductData, keyword: str) -> int:
    keyword = _normalize_keyword(keyword)
    score = 0
    if keyword in product.name.casefold():
        score += 3
    if keyword in product.description.casefold():
        score += 2
    score += sum(keyword in tag.casefold() for tag in product.tags)
    return score


def _normalize_keyword(keyword: str) -> str:
    """Normalize common conversational suffixes before catalog matching.

    Users naturally ask for terms such as ``杯子`` while a catalog may use the
    shorter product stem ``杯`` (for example, ``陶瓷马克杯``). Removing a
    trailing classifier keeps matching forgiving without turning a structured
    keyword search into an overly broad fuzzy search.
    """

    normalized = keyword.strip().casefold()
    if len(normalized) > 1 and normalized[-1] in {"子", "儿", "类", "款"}:
        normalized = normalized[:-1]
    return normalized
