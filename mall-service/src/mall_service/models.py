"""HTTP models owned by the mall service."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Product(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    id: str
    name: str
    description: str
    price_cents: int = Field(ge=0)
    currency: str
    category: str
    tags: tuple[str, ...]
    availability: Literal["in_stock", "out_of_stock"]
    capacity_ml: int | None = Field(default=None, ge=1)
    material: str | None = None
    has_lid: bool | None = None


class ProductList(BaseModel):
    items: list[Product]


class BrowsingRecord(BaseModel):
    product_id: str
    viewed_at: datetime
    count: int = Field(ge=1)


class OrderRecord(BaseModel):
    product_id: str
    ordered_at: datetime
    quantity: int = Field(ge=1)
    status: Literal["completed", "cancelled", "refunded"]


class CartRecord(BaseModel):
    product_id: str
    added_at: datetime
    quantity: int = Field(ge=1)


class UserContext(BaseModel):
    user_id: int
    available: bool = True
    is_new_user: bool = False
    browsing: list[BrowsingRecord] = Field(default_factory=list)
    orders: list[OrderRecord] = Field(default_factory=list)
    cart: list[CartRecord] = Field(default_factory=list)


class RecommendationList(BaseModel):
    items: list[Product]
    source: Literal["organic", "sponsored"]


class ExternalProfile(BaseModel):
    user_id: int
    source: Literal["recommendation-platform"]
    available: bool = True
    profile: dict[str, object] = Field(default_factory=dict)


OrderStatus = Literal["pending_payment", "paid"]


class OrderItemRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    product_id: str = Field(min_length=1)
    quantity: int = Field(ge=1, le=99)


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(strict=True)

    items: list[OrderItemRequest] = Field(min_length=1, max_length=20)
    client_request_id: str = Field(min_length=1, max_length=200)


class OrderItemSnapshot(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    product_id: str
    name: str
    description: str
    quantity: int = Field(ge=1)
    unit_price_cents: int = Field(ge=0)
    line_total_cents: int = Field(ge=0)
    currency: str


class Order(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True)

    id: str
    user_id: int
    status: OrderStatus
    items: list[OrderItemSnapshot]
    subtotal_cents: int = Field(ge=0)
    total_cents: int = Field(ge=0)
    currency: str
    created_at: datetime
