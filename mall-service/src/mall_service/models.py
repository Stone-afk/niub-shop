"""HTTP models owned by the mall service."""

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


class UserContext(BaseModel):
    user_id: int
    browsing_product_ids: list[str]
    purchased_product_ids: list[str]
    cart_product_ids: list[str]
    profile: dict[str, str]


class RecommendationList(BaseModel):
    items: list[Product]
    source: str
