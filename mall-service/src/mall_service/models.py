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
