"""Deterministic in-memory product catalog."""

from .models import Product


_PRODUCTS = (
    Product(
        id="canvas-tote",
        name="经典帆布袋 / Classic Canvas Tote",
        description="轻量耐用的日常托特包 / A lightweight, durable everyday tote.",
        price_cents=59000,
        currency="TWD",
    ),
    Product(
        id="ceramic-mug",
        name="陶瓷马克杯 / Ceramic Mug",
        description="适合咖啡与茶的简约杯款 / A simple mug for coffee or tea.",
        price_cents=39000,
        currency="TWD",
    ),
)


def list_products() -> tuple[Product, ...]:
    return _PRODUCTS
