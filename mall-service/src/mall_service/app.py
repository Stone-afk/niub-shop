"""FastAPI application for the mall service."""

from fastapi import FastAPI

from .catalog import list_products
from .models import ProductList


app = FastAPI(title="niub mall service")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/products", response_model=ProductList)
def products() -> ProductList:
    return ProductList(items=list(list_products()))
