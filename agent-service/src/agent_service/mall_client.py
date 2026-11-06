"""Validated HTTP client for the mall service."""

from dataclasses import dataclass
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError


class MallServiceUnavailable(RuntimeError):
    """The mall service did not return a usable product catalog."""


Availability = Literal["in_stock", "out_of_stock"]


@dataclass(frozen=True)
class ProductData:
    id: str
    name: str
    description: str
    price_cents: int
    currency: str
    category: str
    tags: tuple[str, ...]
    availability: Availability


@dataclass(frozen=True)
class UserContextData:
    user_id: int
    browsing_product_ids: tuple[str, ...]
    purchased_product_ids: tuple[str, ...]
    cart_product_ids: tuple[str, ...]
    profile: dict[str, str]


@dataclass(frozen=True)
class CandidateProducts:
    source: Literal["organic", "sponsored"]
    items: tuple[ProductData, ...]


class _ProductPayload(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str
    name: str
    description: str
    price_cents: int
    currency: str
    category: str
    tags: list[str]
    availability: Availability


class _ProductListPayload(BaseModel):
    model_config = ConfigDict(strict=True)

    items: list[_ProductPayload]


class _UserContextPayload(BaseModel):
    model_config = ConfigDict(strict=True)

    user_id: int
    browsing_product_ids: list[str]
    purchased_product_ids: list[str]
    cart_product_ids: list[str]
    profile: dict[str, str]


class _RecommendationPayload(_ProductListPayload):
    source: Literal["organic", "sponsored"]


class MallClient:
    def __init__(
        self,
        base_url: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 5.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport
        self._timeout = timeout

    def list_products(self) -> list[ProductData]:
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=self._timeout,
                trust_env=False,
            ) as client:
                response = client.get(f"{self._base_url}/products")
                response.raise_for_status()
                payload = _ProductListPayload.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError) as error:
            raise MallServiceUnavailable from error

        return [
            ProductData(
                id=item.id,
                name=item.name,
                description=item.description,
                price_cents=item.price_cents,
                currency=item.currency,
                category=item.category,
                tags=tuple(item.tags),
                availability=item.availability,
            )
            for item in payload.items
        ]

    def get_user_context(self, user_id: int) -> UserContextData:
        payload = self._get_json(f"/users/{user_id}/context", _UserContextPayload)
        return UserContextData(
            user_id=payload.user_id,
            browsing_product_ids=tuple(payload.browsing_product_ids),
            purchased_product_ids=tuple(payload.purchased_product_ids),
            cart_product_ids=tuple(payload.cart_product_ids),
            profile=dict(payload.profile),
        )

    def get_recommendations(self, user_id: int) -> CandidateProducts:
        payload = self._get_json(
            f"/users/{user_id}/recommendations", _RecommendationPayload
        )
        return CandidateProducts("organic", tuple(self._products(payload.items)))

    def get_ad_recommendations(self, user_id: int) -> CandidateProducts:
        payload = self._get_json(
            f"/users/{user_id}/ads/recommendations", _RecommendationPayload
        )
        return CandidateProducts("sponsored", tuple(self._products(payload.items)))

    def _get_json(self, path: str, schema: type[BaseModel]) -> BaseModel:
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=self._timeout,
                trust_env=False,
            ) as client:
                response = client.get(f"{self._base_url}{path}")
                response.raise_for_status()
                return schema.model_validate(response.json())
        except (httpx.HTTPError, ValueError, ValidationError) as error:
            raise MallServiceUnavailable from error

    @staticmethod
    def _products(items: list[_ProductPayload]) -> list[ProductData]:
        return [
            ProductData(
                id=item.id,
                name=item.name,
                description=item.description,
                price_cents=item.price_cents,
                currency=item.currency,
                category=item.category,
                tags=tuple(item.tags),
                availability=item.availability,
            )
            for item in items
        ]
