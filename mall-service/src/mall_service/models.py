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
