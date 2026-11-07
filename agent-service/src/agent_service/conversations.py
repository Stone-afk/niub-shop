"""SQLite persistence for locally owned agent conversations."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypedDict, cast
from uuid import uuid4

from .plan_state import PlanState


TurnStatus = Literal["pending", "completed", "failed"]


class ConversationNotFound(Exception):
    """The requested conversation is absent from the user's scope."""


class ConversationBusy(Exception):
    """The conversation already has an active turn."""


class TurnNotRetryable(Exception):
    """The requested turn is not the latest failed turn."""


class ModelMessage(TypedDict):
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class Turn:
    id: str
    conversation_id: str
    user_content: str
    assistant_content: str | None
    status: TurnStatus
    error_code: str | None
    created_at: str
    updated_at: str
    recommended_product_ids: tuple[str, ...] = ()
    recommended_product_sources: tuple[str, ...] = ()
    plan_state: dict[str, object] | None = None
    orders: tuple[dict[str, str], ...] = ()


@dataclass(frozen=True)
class ConversationSummary:
    id: str
    user_id: int
    title: str
    created_at: str
    updated_at: str
    last_turn_status: TurnStatus | None


@dataclass(frozen=True)
class Conversation:
    id: str
    user_id: int
    title: str
    created_at: str
    updated_at: str
    turns: tuple[Turn, ...]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationRepository:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

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
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            with connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS conversations (
                        id TEXT PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        title TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS conversations_by_user_activity
                        ON conversations(user_id, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS turns (
                        id TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL,
                        user_content TEXT NOT NULL,
                        assistant_content TEXT,
                        status TEXT NOT NULL
                            CHECK(status IN ('pending', 'completed', 'failed')),
                        error_code TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                            ON DELETE CASCADE
                    );
                    CREATE INDEX IF NOT EXISTS turns_by_conversation_created
                        ON turns(conversation_id, created_at);
                    CREATE UNIQUE INDEX IF NOT EXISTS one_pending_turn_per_conversation
                        ON turns(conversation_id) WHERE status = 'pending';

                    CREATE TABLE IF NOT EXISTS turn_recommendations (
                        turn_id TEXT NOT NULL,
                        product_id TEXT NOT NULL CHECK(length(product_id) > 0),
                        position INTEGER NOT NULL CHECK(position >= 0),
                        PRIMARY KEY (turn_id, product_id),
                        UNIQUE (turn_id, position),
                        FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE CASCADE
                    );
                    CREATE TABLE IF NOT EXISTS turn_recommendation_sources (
                        turn_id TEXT NOT NULL,
                        product_id TEXT NOT NULL,
                        source TEXT NOT NULL CHECK(source IN ('organic', 'sponsored')),
                        PRIMARY KEY (turn_id, product_id),
                        FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE CASCADE
                    );
                    CREATE TABLE IF NOT EXISTS turn_plans (
                        turn_id TEXT PRIMARY KEY,
                        plan_id TEXT NOT NULL,
                        goal TEXT NOT NULL,
                        status TEXT NOT NULL CHECK(status IN (
                            'draft', 'in_progress', 'completed', 'blocked', 'cancelled'
                        )),
                        current_step_id TEXT,
                        state_json TEXT NOT NULL,
                        version INTEGER NOT NULL CHECK(version >= 1),
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE CASCADE
                    );
                    CREATE TABLE IF NOT EXISTS turn_orders (
                        turn_id TEXT NOT NULL,
                        order_id TEXT NOT NULL,
                        preview_path TEXT NOT NULL,
                        status TEXT NOT NULL,
                        position INTEGER NOT NULL CHECK(position >= 0),
                        PRIMARY KEY(turn_id, order_id),
                        UNIQUE(turn_id, position),
                        FOREIGN KEY(turn_id) REFERENCES turns(id) ON DELETE CASCADE
                    );
                    """
                )

    def create_conversation(self, user_id: int) -> ConversationSummary:
        identifier = str(uuid4())
        now = _utc_now()
        with self._connection() as connection, connection:
            connection.execute(
                """INSERT INTO conversations
                   (id, user_id, title, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (identifier, user_id, "新对话 / New conversation", now, now),
            )
        return ConversationSummary(
            identifier,
            user_id,
            "新对话 / New conversation",
            now,
            now,
            None,
        )

    def list_conversations(self, user_id: int) -> list[ConversationSummary]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT c.*,
                          (SELECT status FROM turns t
                           WHERE t.conversation_id = c.id
                           ORDER BY t.created_at DESC, t.rowid DESC LIMIT 1)
                          AS last_turn_status
                   FROM conversations c
                   WHERE c.user_id = ?
                   ORDER BY c.updated_at DESC, c.rowid DESC""",
                (user_id,),
            ).fetchall()
        return [self._summary(row) for row in rows]

    def delete_conversation(self, user_id: int, conversation_id: str) -> None:
        with self._connection() as connection, connection:
            self._conversation_row(connection, user_id, conversation_id)
            connection.execute(
                "DELETE FROM conversations WHERE id = ? AND user_id = ?",
                (conversation_id, user_id),
            )

    def get_conversation(self, user_id: int, conversation_id: str) -> Conversation:
        with self._connection() as connection:
            row = self._conversation_row(connection, user_id, conversation_id)
            turn_rows = connection.execute(
                """SELECT * FROM turns WHERE conversation_id = ?
                   ORDER BY created_at, rowid""",
                (conversation_id,),
            ).fetchall()
            recommendation_rows = connection.execute(
                """SELECT r.turn_id, r.product_id,
                          COALESCE(s.source, 'organic') AS source
                   FROM turn_recommendations r
                   LEFT JOIN turn_recommendation_sources s
                     ON s.turn_id = r.turn_id AND s.product_id = r.product_id
                   JOIN turns t ON t.id = r.turn_id
                   WHERE t.conversation_id = ?
                   ORDER BY r.turn_id, r.position""",
                (conversation_id,),
            ).fetchall()
            plan_rows = connection.execute(
                "SELECT * FROM turn_plans WHERE turn_id IN "
                "(SELECT id FROM turns WHERE conversation_id = ?)",
                (conversation_id,),
            ).fetchall()
            order_rows = connection.execute(
                "SELECT * FROM turn_orders WHERE turn_id IN "
                "(SELECT id FROM turns WHERE conversation_id = ?) "
                "ORDER BY turn_id, position",
                (conversation_id,),
            ).fetchall()
        recommendations: dict[str, list[tuple[str, str]]] = {}
        for recommendation in recommendation_rows:
            recommendations.setdefault(recommendation["turn_id"], []).append(
                (recommendation["product_id"], recommendation["source"])
            )
        plans = {
            plan["turn_id"]: json.loads(plan["state_json"])
            for plan in plan_rows
        }
        orders: dict[str, list[dict[str, str]]] = {}
        for order in order_rows:
            orders.setdefault(order["turn_id"], []).append(
                {
                    "order_id": order["order_id"],
                    "preview_path": order["preview_path"],
                    "status": order["status"],
                }
            )
        return Conversation(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            turns=tuple(
                self._turn(
                    item,
                    tuple(product_id for product_id, _source in recommendations.get(item["id"], ())),
                    tuple(source for _product_id, source in recommendations.get(item["id"], ())),
                    plans.get(item["id"]),
                    tuple(orders.get(item["id"], ())),
                )
                for item in turn_rows
            ),
        )

    def create_turn(
        self, user_id: int, conversation_id: str, content: str
    ) -> Turn:
        identifier = str(uuid4())
        now = _utc_now()
        with self._connection() as connection:
            try:
                with connection:
                    conversation = self._conversation_row(
                        connection, user_id, conversation_id
                    )
                    is_first_turn = connection.execute(
                        "SELECT 1 FROM turns WHERE conversation_id = ? LIMIT 1",
                        (conversation_id,),
                    ).fetchone() is None
                    connection.execute(
                        """INSERT INTO turns
                           (id, conversation_id, user_content, assistant_content,
                            status, error_code, created_at, updated_at)
                           VALUES (?, ?, ?, NULL, 'pending', NULL, ?, ?)""",
                        (identifier, conversation_id, content, now, now),
                    )
                    title = conversation["title"]
                    if is_first_turn:
                        title = content[:60]
                    connection.execute(
                        """UPDATE conversations SET title = ?, updated_at = ?
                           WHERE id = ? AND user_id = ?""",
                        (title, now, conversation_id, user_id),
                    )
            except sqlite3.IntegrityError as error:
                if "turns.conversation_id" in str(error):
                    raise ConversationBusy("conversation has a pending turn") from None
                raise
        return Turn(
            identifier,
            conversation_id,
            content,
            None,
            "pending",
            None,
            now,
            now,
        )

    def complete_turn(
        self,
        user_id: int,
        conversation_id: str,
        turn_id: str,
        assistant_content: str,
        recommended_product_ids: Sequence[str] = (),
        recommended_product_sources: Sequence[str] = (),
        plan_state: PlanState | None = None,
        plan_version: int = 1,
        orders: Sequence[dict[str, str]] = (),
    ) -> Turn:
        if not assistant_content:
            raise ValueError("assistant content must not be empty")
        if plan_version < 1:
            raise ValueError("plan version must be positive")
        product_ids = tuple(recommended_product_ids)
        if len(product_ids) > 3:
            raise ValueError("at most three recommended product IDs are allowed")
        if any(not isinstance(product_id, str) or not product_id for product_id in product_ids):
            raise ValueError("recommended product IDs must be non-empty strings")
        if len(set(product_ids)) != len(product_ids):
            raise ValueError("recommended product IDs must be unique")
        sources = tuple(recommended_product_sources)
        if sources and len(sources) != len(product_ids):
            raise ValueError("recommended product sources must match product IDs")
        if not sources:
            sources = ("organic",) * len(product_ids)
        if any(source not in {"organic", "sponsored"} for source in sources):
            raise ValueError("recommended product sources are invalid")

        now = _utc_now()
        with self._connection() as connection, connection:
            self._conversation_row(connection, user_id, conversation_id)
            cursor = connection.execute(
                """UPDATE turns SET status = 'completed', assistant_content = ?,
                   error_code = NULL, updated_at = ?
                   WHERE id = ? AND conversation_id = ? AND status = 'pending'""",
                (assistant_content, now, turn_id, conversation_id),
            )
            if cursor.rowcount != 1:
                raise ConversationNotFound("turn not found")
            connection.execute(
                "DELETE FROM turn_recommendations WHERE turn_id = ?", (turn_id,)
            )
            connection.execute(
                "DELETE FROM turn_recommendation_sources WHERE turn_id = ?", (turn_id,)
            )
            connection.execute("DELETE FROM turn_plans WHERE turn_id = ?", (turn_id,))
            connection.execute("DELETE FROM turn_orders WHERE turn_id = ?", (turn_id,))
            connection.executemany(
                """INSERT INTO turn_recommendations
                   (turn_id, product_id, position) VALUES (?, ?, ?)""",
                (
                    (turn_id, product_id, position)
                    for position, product_id in enumerate(product_ids)
                ),
            )
            connection.executemany(
                """INSERT INTO turn_recommendation_sources
                   (turn_id, product_id, source) VALUES (?, ?, ?)""",
                (
                    (turn_id, product_id, source)
                    for product_id, source in zip(product_ids, sources)
                ),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            if plan_state is not None:
                state = plan_state.model_dump(mode="json", exclude_none=False)
                connection.execute(
                    """INSERT INTO turn_plans
                       (turn_id, plan_id, goal, status, current_step_id,
                        state_json, version, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        turn_id,
                        plan_state.plan_id,
                        plan_state.goal,
                        plan_state.status,
                        plan_state.current_step_id,
                        json.dumps(state, ensure_ascii=False),
                        plan_version,
                        now,
                        now,
                    ),
                )
            connection.executemany(
                """INSERT INTO turn_orders
                   (turn_id, order_id, preview_path, status, position)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    (
                        turn_id,
                        order["order_id"],
                        order["preview_path"],
                        order["status"],
                        position,
                    )
                    for position, order in enumerate(orders)
                ),
            )
            row = connection.execute(
                "SELECT * FROM turns WHERE id = ?", (turn_id,)
            ).fetchone()
        return self._turn(
            row,
            product_ids,
            sources,
            plan_state.model_dump(mode="json") if plan_state else None,
            tuple(orders),
        )

    def fail_turn(
        self,
        user_id: int,
        conversation_id: str,
        turn_id: str,
        error_code: str,
    ) -> Turn:
        return self._transition(
            user_id,
            conversation_id,
            turn_id,
            expected="pending",
            status="failed",
            assistant_content=None,
            error_code=error_code,
        )

    def prepare_retry(
        self, user_id: int, conversation_id: str, turn_id: str
    ) -> Turn:
        with self._connection() as connection, connection:
            self._conversation_row(connection, user_id, conversation_id)
            row = connection.execute(
                """SELECT * FROM turns WHERE id = ? AND conversation_id = ?""",
                (turn_id, conversation_id),
            ).fetchone()
            latest = connection.execute(
                """SELECT id FROM turns WHERE conversation_id = ?
                   ORDER BY created_at DESC, rowid DESC LIMIT 1""",
                (conversation_id,),
            ).fetchone()
            if row is None or row["status"] != "failed" or latest["id"] != turn_id:
                raise TurnNotRetryable("turn is not retryable")
            now = _utc_now()
            try:
                connection.execute(
                    "DELETE FROM turn_recommendations WHERE turn_id = ?", (turn_id,)
                )
                connection.execute(
                    "DELETE FROM turn_recommendation_sources WHERE turn_id = ?", (turn_id,)
                )
                connection.execute("DELETE FROM turn_plans WHERE turn_id = ?", (turn_id,))
                connection.execute("DELETE FROM turn_orders WHERE turn_id = ?", (turn_id,))
                connection.execute(
                    """UPDATE turns SET status = 'pending', assistant_content = NULL,
                       error_code = NULL, updated_at = ? WHERE id = ?""",
                    (now, turn_id),
                )
            except sqlite3.IntegrityError:
                raise ConversationBusy("conversation has a pending turn") from None
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            updated = connection.execute(
                "SELECT * FROM turns WHERE id = ?", (turn_id,)
            ).fetchone()
        return self._turn(updated)

    def model_messages(
        self, user_id: int, conversation_id: str, active_turn_id: str
    ) -> list[ModelMessage]:
        with self._connection() as connection:
            self._conversation_row(connection, user_id, conversation_id)
            active = connection.execute(
                """SELECT * FROM turns WHERE id = ? AND conversation_id = ?
                   AND status = 'pending'""",
                (active_turn_id, conversation_id),
            ).fetchone()
            if active is None:
                raise ConversationNotFound("active turn not found")
            completed = connection.execute(
                """SELECT * FROM turns WHERE conversation_id = ?
                   AND status = 'completed' AND id != ?
                   ORDER BY created_at, rowid""",
                (conversation_id, active_turn_id),
            ).fetchall()
        messages: list[ModelMessage] = []
        for row in completed:
            messages.append({"role": "user", "content": row["user_content"]})
            messages.append(
                {"role": "assistant", "content": row["assistant_content"]}
            )
        messages.append({"role": "user", "content": active["user_content"]})
        return messages

    def recover_pending(self, user_id: int) -> int:
        now = _utc_now()
        with self._connection() as connection, connection:
            pending_ids = connection.execute(
                """SELECT id FROM turns WHERE status = 'pending'
                   AND conversation_id IN
                       (SELECT id FROM conversations WHERE user_id = ?)""",
                (user_id,),
            ).fetchall()
            connection.executemany(
                "DELETE FROM turn_recommendations WHERE turn_id = ?",
                ((row["id"],) for row in pending_ids),
            )
            connection.executemany(
                "DELETE FROM turn_recommendation_sources WHERE turn_id = ?",
                ((row["id"],) for row in pending_ids),
            )
            connection.executemany(
                "DELETE FROM turn_plans WHERE turn_id = ?",
                ((row["id"],) for row in pending_ids),
            )
            connection.executemany(
                "DELETE FROM turn_orders WHERE turn_id = ?",
                ((row["id"],) for row in pending_ids),
            )
            cursor = connection.execute(
                """UPDATE turns SET status = 'failed', assistant_content = NULL,
                   error_code = 'service_restarted', updated_at = ?
                   WHERE status = 'pending' AND conversation_id IN
                       (SELECT id FROM conversations WHERE user_id = ?)""",
                (now, user_id),
            )
            connection.execute(
                """UPDATE conversations SET updated_at = ? WHERE user_id = ?
                   AND id IN (SELECT conversation_id FROM turns
                              WHERE error_code = 'service_restarted'
                              AND updated_at = ?)""",
                (now, user_id, now),
            )
        return cursor.rowcount

    def _transition(
        self,
        user_id: int,
        conversation_id: str,
        turn_id: str,
        *,
        expected: TurnStatus,
        status: TurnStatus,
        assistant_content: str | None,
        error_code: str | None,
    ) -> Turn:
        now = _utc_now()
        with self._connection() as connection, connection:
            self._conversation_row(connection, user_id, conversation_id)
            cursor = connection.execute(
                """UPDATE turns SET status = ?, assistant_content = ?,
                   error_code = ?, updated_at = ?
                   WHERE id = ? AND conversation_id = ? AND status = ?""",
                (
                    status,
                    assistant_content,
                    error_code,
                    now,
                    turn_id,
                    conversation_id,
                    expected,
                ),
            )
            if cursor.rowcount != 1:
                raise ConversationNotFound("turn not found")
            if status != "completed":
                connection.execute(
                    "DELETE FROM turn_recommendations WHERE turn_id = ?", (turn_id,)
                )
                connection.execute(
                    "DELETE FROM turn_recommendation_sources WHERE turn_id = ?", (turn_id,)
                )
                connection.execute("DELETE FROM turn_plans WHERE turn_id = ?", (turn_id,))
                connection.execute("DELETE FROM turn_orders WHERE turn_id = ?", (turn_id,))
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conversation_id),
            )
            row = connection.execute(
                "SELECT * FROM turns WHERE id = ?", (turn_id,)
            ).fetchone()
        return self._turn(row)

    @staticmethod
    def _conversation_row(
        connection: sqlite3.Connection, user_id: int, conversation_id: str
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM conversations WHERE id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
        if row is None:
            raise ConversationNotFound("conversation not found")
        return row

    @staticmethod
    def _turn(
        row: sqlite3.Row,
        recommended_product_ids: tuple[str, ...] = (),
        recommended_product_sources: tuple[str, ...] = (),
        plan_state: dict[str, object] | None = None,
        orders: tuple[dict[str, str], ...] = (),
    ) -> Turn:
        return Turn(
            id=row["id"],
            conversation_id=row["conversation_id"],
            user_content=row["user_content"],
            assistant_content=row["assistant_content"],
            status=cast(TurnStatus, row["status"]),
            error_code=row["error_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            recommended_product_ids=recommended_product_ids,
            recommended_product_sources=recommended_product_sources,
            plan_state=plan_state,
            orders=orders,
        )

    @staticmethod
    def _summary(row: sqlite3.Row) -> ConversationSummary:
        return ConversationSummary(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_turn_status=cast(TurnStatus | None, row["last_turn_status"]),
        )
