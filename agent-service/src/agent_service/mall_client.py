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
