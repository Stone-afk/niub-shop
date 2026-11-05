"""SQLite persistence for locally owned agent conversations."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypedDict, cast
from uuid import uuid4


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

    def get_conversation(self, user_id: int, conversation_id: str) -> Conversation:
        with self._connection() as connection:
            row = self._conversation_row(connection, user_id, conversation_id)
            turn_rows = connection.execute(
                """SELECT * FROM turns WHERE conversation_id = ?
                   ORDER BY created_at, rowid""",
                (conversation_id,),
            ).fetchall()
        return Conversation(
            id=row["id"],
            user_id=row["user_id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            turns=tuple(self._turn(item) for item in turn_rows),
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
                    connection.execute(
                        """INSERT INTO turns
                           (id, conversation_id, user_content, assistant_content,
                            status, error_code, created_at, updated_at)
                           VALUES (?, ?, ?, NULL, 'pending', NULL, ?, ?)""",
                        (identifier, conversation_id, content, now, now),
                    )
                    title = conversation["title"]
                    if title == "新对话 / New conversation":
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
    ) -> Turn:
        if not assistant_content:
            raise ValueError("assistant content must not be empty")
        return self._transition(
            user_id,
            conversation_id,
            turn_id,
            expected="pending",
            status="completed",
            assistant_content=assistant_content,
            error_code=None,
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
    def _turn(row: sqlite3.Row) -> Turn:
        return Turn(
            id=row["id"],
            conversation_id=row["conversation_id"],
            user_content=row["user_content"],
            assistant_content=row["assistant_content"],
            status=cast(TurnStatus, row["status"]),
            error_code=row["error_code"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
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
