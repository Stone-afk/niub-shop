"""HTTP models owned by the mall service."""

from pydantic import BaseModel, Field


class Product(BaseModel):
    id: str
    name: str
    description: str
    price_cents: int = Field(ge=0)
    currency: str


class ProductList(BaseModel):
    items: list[Product]
