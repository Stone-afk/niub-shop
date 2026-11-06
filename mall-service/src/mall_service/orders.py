"""SQLite-backed mock order storage owned by the mall service."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .catalog import list_products
from .models import CreateOrderRequest, Order, OrderItemSnapshot


class UnknownProductError(ValueError):
    """The requested product is not in the catalog."""


class ProductOutOfStockError(ValueError):
    """The requested product cannot be ordered."""


class OrderNotFoundError(LookupError):
    """The requested order is not available."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class OrderStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    client_request_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending_payment', 'paid')),
                    currency TEXT NOT NULL,
                    subtotal_cents INTEGER NOT NULL,
                    total_cents INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(user_id, client_request_id)
                );
                CREATE TABLE IF NOT EXISTS order_items (
                    order_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    product_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    unit_price_cents INTEGER NOT NULL,
                    line_total_cents INTEGER NOT NULL,
                    currency TEXT NOT NULL,
                    PRIMARY KEY(order_id, position),
                    FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
                );
                """
            )

    def create(self, user_id: int, request: CreateOrderRequest) -> tuple[Order, bool]:
        products = {product.id: product for product in list_products()}
        snapshots: list[OrderItemSnapshot] = []
        seen: set[str] = set()
        for item in request.items:
            if item.product_id in seen:
                raise ValueError("duplicate product IDs are not allowed")
            seen.add(item.product_id)
            product = products.get(item.product_id)
            if product is None:
                raise UnknownProductError(item.product_id)
            if product.availability != "in_stock":
                raise ProductOutOfStockError(item.product_id)
            snapshots.append(
                OrderItemSnapshot(
                    product_id=product.id,
                    name=product.name,
                    description=product.description,
                    quantity=item.quantity,
                    unit_price_cents=product.price_cents,
                    line_total_cents=product.price_cents * item.quantity,
                    currency=product.currency,
                )
            )

        with self._connection() as connection, connection:
            existing = connection.execute(
                "SELECT id FROM orders WHERE user_id = ? AND client_request_id = ?",
                (user_id, request.client_request_id),
            ).fetchone()
            if existing is not None:
                return self.get(user_id, existing["id"], connection), False

            order_id = f"ord-{uuid4().hex[:16]}"
            created_at = _utc_now()
            subtotal = sum(item.line_total_cents for item in snapshots)
            currency = snapshots[0].currency
            connection.execute(
                """INSERT INTO orders
                   (id, user_id, client_request_id, status, currency,
                    subtotal_cents, total_cents, created_at)
                   VALUES (?, ?, ?, 'pending_payment', ?, ?, ?, ?)""",
                (
                    order_id,
                    user_id,
                    request.client_request_id,
                    currency,
                    subtotal,
                    subtotal,
                    created_at,
                ),
            )
            connection.executemany(
                """INSERT INTO order_items
                   (order_id, position, product_id, name, description, quantity,
                    unit_price_cents, line_total_cents, currency)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    (
                        order_id,
                        position,
                        item.product_id,
                        item.name,
                        item.description,
                        item.quantity,
                        item.unit_price_cents,
                        item.line_total_cents,
                        item.currency,
                    )
                    for position, item in enumerate(snapshots)
                ),
            )
            return self.get(user_id, order_id, connection), True

    def get(
        self,
        user_id: int,
        order_id: str,
        connection: sqlite3.Connection | None = None,
    ) -> Order:
        if connection is None:
            with self._connection() as owned:
                return self.get(user_id, order_id, owned)
        row = connection.execute(
            "SELECT * FROM orders WHERE id = ? AND user_id = ?",
            (order_id, user_id),
        ).fetchone()
        if row is None:
            raise OrderNotFoundError(order_id)
        item_rows = connection.execute(
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY position",
            (order_id,),
        ).fetchall()
        return Order(
            id=row["id"],
            user_id=row["user_id"],
            status=row["status"],
            items=[
                OrderItemSnapshot(
                    product_id=item["product_id"],
                    name=item["name"],
                    description=item["description"],
                    quantity=item["quantity"],
                    unit_price_cents=item["unit_price_cents"],
                    line_total_cents=item["line_total_cents"],
                    currency=item["currency"],
                )
                for item in item_rows
            ],
            subtotal_cents=row["subtotal_cents"],
            total_cents=row["total_cents"],
            currency=row["currency"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def pay(self, user_id: int, order_id: str) -> Order:
        with self._connection() as connection, connection:
            self.get(user_id, order_id, connection)
            connection.execute(
                "UPDATE orders SET status = 'paid' WHERE id = ? AND user_id = ?",
                (order_id, user_id),
            )
            return self.get(user_id, order_id, connection)
