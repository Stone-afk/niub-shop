from mall_service.catalog import list_products


def test_catalog_contains_multiple_in_stock_drinkware_options_with_specs() -> None:
    products = [
        product
        for product in list_products()
        if product.category == "drinkware" and product.availability == "in_stock"
    ]

    assert len(products) >= 4
    assert len({product.id for product in products}) == len(products)
    assert {product.material for product in products} >= {
        "ceramic",
        "glass",
        "stainless_steel",
    }
    assert all(product.capacity_ml is not None for product in products)


def test_catalog_has_cup_options_across_price_and_capacity_ranges() -> None:
    products = [
        product
        for product in list_products()
        if product.category == "drinkware" and product.availability == "in_stock"
    ]

    assert any(product.price_cents <= 50000 for product in products)
    assert any(product.price_cents > 50000 for product in products)
    assert any(product.capacity_ml and product.capacity_ml <= 400 for product in products)
    assert any(product.capacity_ml and product.capacity_ml >= 500 for product in products)
