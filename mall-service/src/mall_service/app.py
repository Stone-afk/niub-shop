"""FastAPI application for the mall service."""

from fastapi import FastAPI

from .catalog import list_products
from .models import (
    BrowsingRecord,
    CartRecord,
    ExternalProfile,
    OrderRecord,
    ProductList,
    RecommendationList,
    UserContext,
)


app = FastAPI(title="niub mall service")


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
    products = list_products()
    return RecommendationList(
        source="organic",
        items=[products[1], products[0]],
    )


@app.get("/users/{user_id}/ads/recommendations", response_model=RecommendationList)
def ad_recommendations(user_id: int) -> RecommendationList:
    del user_id
    products = list_products()
    return RecommendationList(source="sponsored", items=[products[0], products[1]])
