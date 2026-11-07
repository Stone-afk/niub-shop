"""FastAPI application for the mall service."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .catalog import list_products
from .models import (
    BrowsingRecord,
    CartRecord,
    CreateOrderRequest,
    ExternalProfile,
    Order,
    OrderRecord,
    ProductList,
    RecommendationList,
    UserContext,
)
from .orders import (
    OrderNotFoundError,
    OrderStore,
    ProductOutOfStockError,
    UnknownProductError,
)


app = FastAPI(title="niub mall service")
order_store = OrderStore(os.getenv("MALL_DB_FILE", "data/mall.db"))


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/products", response_model=ProductList)
def products() -> ProductList:
    return ProductList(items=list(list_products()))


@app.get("/users/{user_id}/context", response_model=UserContext)
def user_context(user_id: int) -> UserContext:
    return UserContext(
        user_id=user_id,
        available=True,
        is_new_user=False,
        browsing=[
            BrowsingRecord(
                product_id="ceramic-mug",
                viewed_at="2026-11-06T10:00:00Z",
                count=3,
            ),
            BrowsingRecord(
                product_id="canvas-tote",
                viewed_at="2026-11-05T10:00:00Z",
                count=1,
            ),
        ],
        orders=[
            OrderRecord(
                product_id="canvas-tote",
                ordered_at="2026-10-20T10:00:00Z",
                quantity=1,
                status="completed",
            )
        ],
        cart=[
            CartRecord(
                product_id="ceramic-mug",
                added_at="2026-11-06T09:00:00Z",
                quantity=1,
            )
        ],
    )


@app.get("/users/{user_id}/external-profile", response_model=ExternalProfile)
def external_profile(user_id: int) -> ExternalProfile:
    return ExternalProfile(
        user_id=user_id,
        source="recommendation-platform",
        available=True,
        profile={
            "interest_categories": ["drinkware", "home"],
            "interest_tags": ["coffee"],
            "price_sensitivity": "medium",
            "preferred_availability": "in_stock",
        },
    )


@app.get("/users/{user_id}/recommendations", response_model=RecommendationList)
def recommendations(user_id: int) -> RecommendationList:
    del user_id
    products = {product.id: product for product in list_products()}
    return RecommendationList(
        source="organic",
        items=[
            products["ceramic-mug"],
            products["glass-coffee-cup"],
            products["office-mug-with-lid"],
        ],
    )


@app.get("/users/{user_id}/ads/recommendations", response_model=RecommendationList)
def ad_recommendations(user_id: int) -> RecommendationList:
    del user_id
    products = {product.id: product for product in list_products()}
    return RecommendationList(
        source="sponsored",
        items=[products["stainless-travel-tumbler"]],
    )


@app.post("/orders", response_model=Order, status_code=201)
def create_order(request: CreateOrderRequest):
    try:
        order, created = order_store.create(1, request)
    except UnknownProductError as error:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "unknown_product", "message": str(error)}},
        )
    except ProductOutOfStockError as error:
        return JSONResponse(
            status_code=409,
            content={"error": {"code": "out_of_stock", "message": str(error)}},
        )
    except ValueError as error:
        return JSONResponse(
            status_code=400,
            content={"error": {"code": "invalid_order", "message": str(error)}},
        )
    return JSONResponse(
        status_code=201 if created else 200,
        content=order.model_dump(mode="json"),
    )


@app.get("/orders/{order_id}/preview", response_model=Order)
def order_preview(order_id: str):
    try:
        return order_store.get(1, order_id)
    except OrderNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "order_not_found", "message": "Order not found"}},
        )


@app.post("/orders/{order_id}/pay", response_model=Order)
def pay_order(order_id: str):
    try:
        return order_store.pay(1, order_id)
    except OrderNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "order_not_found", "message": "Order not found"}},
        )
