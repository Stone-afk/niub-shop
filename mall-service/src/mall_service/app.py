"""FastAPI application for the mall service."""

from fastapi import FastAPI

from .catalog import list_products
from .models import ProductList, RecommendationList, UserContext


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
        browsing_product_ids=["ceramic-mug", "canvas-tote"],
        purchased_product_ids=["canvas-tote"],
        cart_product_ids=["ceramic-mug"],
        profile={"favorite_category": "drinkware", "favorite_tag": "coffee"},
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
