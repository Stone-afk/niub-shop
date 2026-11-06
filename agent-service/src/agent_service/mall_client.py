"""Validated HTTP client for the mall service."""

from dataclasses import dataclass
from typing import Any, Literal, Sequence

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError


class MallServiceUnavailable(RuntimeError):
    """The mall service did not return a usable product catalog."""


class MallOrderRejected(RuntimeError):
    """The mall rejected an otherwise well-formed order request."""


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
    available: bool
    is_new_user: bool
    browsing: tuple[dict[str, Any], ...]
    orders: tuple[dict[str, Any], ...]
    cart: tuple[dict[str, Any], ...]

    @property
    def purchased_product_ids(self) -> tuple[str, ...]:
        return tuple(
            item["product_id"]
            for item in self.orders
            if item.get("status") == "completed"
        )

    @property
    def cart_product_ids(self) -> tuple[str, ...]:
        return tuple(item["product_id"] for item in self.cart)


@dataclass(frozen=True)
class ExternalProfileData:
    user_id: int
    source: str
    available: bool
    profile: dict[str, Any]


@dataclass(frozen=True)
class CandidateProducts:
    source: Literal["organic", "sponsored"]
    items: tuple[ProductData, ...]


OrderStatus = Literal["pending_payment", "paid"]


@dataclass(frozen=True)
class OrderData:
    id: str
    user_id: int
    status: OrderStatus
    items: tuple[dict[str, Any], ...]
    subtotal_cents: int
    total_cents: int
    currency: str
    created_at: str


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
    available: bool = True
    is_new_user: bool = False
    browsing: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    cart: list[dict[str, Any]]


class _ExternalProfilePayload(BaseModel):
    model_config = ConfigDict(strict=True)

    user_id: int
    source: Literal["recommendation-platform"]
    available: bool = True
    profile: dict[str, Any]


class _RecommendationPayload(_ProductListPayload):
    source: Literal["organic", "sponsored"]


class _OrderPayload(BaseModel):
    model_config = ConfigDict(strict=True)

    id: str
    user_id: int
    status: OrderStatus
    items: list[dict[str, Any]]
    subtotal_cents: int
    total_cents: int
    currency: str
    created_at: str


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
            available=payload.available,
            is_new_user=payload.is_new_user,
            browsing=tuple(payload.browsing),
            orders=tuple(payload.orders),
            cart=tuple(payload.cart),
        )

    def get_external_profile(self, user_id: int) -> ExternalProfileData:
        payload = self._get_json(
            f"/users/{user_id}/external-profile", _ExternalProfilePayload
        )
        return ExternalProfileData(
            user_id=payload.user_id,
            source=payload.source,
            available=payload.available,
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

    def create_order(
        self,
        items: Sequence[dict[str, int | str]],
        client_request_id: str,
    ) -> OrderData:
        try:
            payload = self._post_json(
                "/orders",
                {"items": list(items), "client_request_id": client_request_id},
                _OrderPayload,
            )
        except MallOrderRejected:
            raise
        return OrderData(
            id=payload.id,
            user_id=payload.user_id,
            status=payload.status,
            items=tuple(dict(item) for item in payload.items),
            subtotal_cents=payload.subtotal_cents,
            total_cents=payload.total_cents,
            currency=payload.currency,
            created_at=payload.created_at,
        )

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

    def _post_json(
        self,
        path: str,
        body: dict[str, object],
        schema: type[BaseModel],
    ) -> BaseModel:
        try:
            with httpx.Client(
                transport=self._transport,
                timeout=self._timeout,
                trust_env=False,
            ) as client:
                response = client.post(f"{self._base_url}{path}", json=body)
                response.raise_for_status()
                return schema.model_validate(response.json())
        except httpx.HTTPStatusError as error:
            if 400 <= error.response.status_code < 500:
                try:
                    detail = error.response.json()
                except ValueError:
                    detail = {"code": "order_rejected"}
                raise MallOrderRejected(str(detail)) from error
            raise MallServiceUnavailable from error
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
