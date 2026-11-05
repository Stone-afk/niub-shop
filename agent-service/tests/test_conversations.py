import sqlite3
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from agent_service.conversations import (
    ConversationBusy,
    ConversationNotFound,
    ConversationRepository,
    TurnNotRetryable,
)


def repository(tmp_path: Path) -> ConversationRepository:
    result = ConversationRepository(tmp_path / "data" / "agent.db")
    result.initialize()
    return result


def test_initializes_sqlite_pragmas_and_schema(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "agent.db"
    repo = ConversationRepository(database)

    repo.initialize()

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
    assert {"conversations", "turns", "turn_recommendations"} <= tables
    assert "one_pending_turn_per_conversation" in indexes


def test_creates_lists_and_reads_fixed_user_conversation(tmp_path: Path) -> None:
    repo = repository(tmp_path)

    summary = repo.create_conversation(1)
    detail = repo.get_conversation(1, summary.id)

    UUID(summary.id)
    datetime.fromisoformat(summary.created_at)
    assert summary.user_id == 1
    assert summary.title == "新对话 / New conversation"
    assert summary.last_turn_status is None
    assert repo.list_conversations(1) == [summary]
    assert detail.id == summary.id
    assert detail.user_id == 1
    assert detail.turns == ()


def test_first_turn_sets_truncated_title_and_recent_order(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    older = repo.create_conversation(1)
    newer = repo.create_conversation(1)
    long_content = "对" * 61

    first = repo.create_turn(1, older.id, long_content)
    second = repo.create_turn(1, newer.id, "第二个会话")

    UUID(first.id)
    assert repo.list_conversations(1)[0].id == newer.id
    assert repo.get_conversation(1, older.id).title == "对" * 60
    assert repo.get_conversation(1, older.id).turns == (first,)
    assert repo.get_conversation(1, newer.id).turns == (second,)


def test_default_title_text_as_first_message_is_not_replaced_by_second_turn(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    conversation = repo.create_conversation(1)
    first = repo.create_turn(1, conversation.id, "新对话 / New conversation")
    repo.complete_turn(1, conversation.id, first.id, "第一答")

    repo.create_turn(1, conversation.id, "第二问")

    assert repo.get_conversation(1, conversation.id).title == "新对话 / New conversation"


def test_every_lookup_is_scoped_by_user_id(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    conversation = repo.create_conversation(1)
    turn = repo.create_turn(1, conversation.id, "秘密内容")

    assert repo.list_conversations(2) == []
    for action in (
        lambda: repo.get_conversation(2, conversation.id),
        lambda: repo.create_turn(2, conversation.id, "越权"),
        lambda: repo.complete_turn(2, conversation.id, turn.id, "越权"),
        lambda: repo.fail_turn(2, conversation.id, turn.id, "hidden"),
        lambda: repo.model_messages(2, conversation.id, turn.id),
    ):
        with pytest.raises(ConversationNotFound) as error:
            action()
        assert "秘密内容" not in str(error.value)


def test_partial_unique_index_rejects_second_pending_turn(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    conversation = repo.create_conversation(1)
    repo.create_turn(1, conversation.id, "第一轮")

    with pytest.raises(ConversationBusy):
        repo.create_turn(1, conversation.id, "第二轮")

    assert len(repo.get_conversation(1, conversation.id).turns) == 1


def test_complete_turn_persists_full_answer_atomically(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    conversation = repo.create_conversation(1)
    turn = repo.create_turn(1, conversation.id, "问题")

    completed = repo.complete_turn(
        1, conversation.id, turn.id, "完整回答", ["second", "first"]
    )

    assert completed.status == "completed"
    assert completed.assistant_content == "完整回答"
    assert completed.recommended_product_ids == ("second", "first")
    stored = repo.get_conversation(1, conversation.id).turns[0]
    assert stored == completed
    assert repo.list_conversations(1)[0].last_turn_status == "completed"


def test_recommendation_schema_enforces_position_product_and_cascade(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    conversation = repo.create_conversation(1)
    turn = repo.create_turn(1, conversation.id, "问题")

    with sqlite3.connect(repo.path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for product_id, position in (("", 0), ("valid", -1)):
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO turn_recommendations VALUES (?, ?, ?)",
                    (turn.id, product_id, position),
                )
        connection.execute(
            "INSERT INTO turn_recommendations VALUES (?, ?, ?)",
            (turn.id, "one", 0),
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO turn_recommendations VALUES (?, ?, ?)",
                (turn.id, "two", 0),
            )
        connection.execute("DELETE FROM conversations WHERE id = ?", (conversation.id,))
        assert connection.execute(
            "SELECT * FROM turn_recommendations"
        ).fetchall() == []


def test_duplicate_recommendations_are_rejected_before_turn_completion(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    conversation = repo.create_conversation(1)
    turn = repo.create_turn(1, conversation.id, "问题")

    with pytest.raises(ValueError, match="unique"):
        repo.complete_turn(1, conversation.id, turn.id, "回答", ["same", "same"])

    stored = repo.get_conversation(1, conversation.id).turns[0]
    assert stored.status == "pending"
    assert stored.assistant_content is None
    assert stored.recommended_product_ids == ()


def test_recommendation_insert_failure_rolls_back_completed_turn(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    conversation = repo.create_conversation(1)
    turn = repo.create_turn(1, conversation.id, "问题")
    with sqlite3.connect(repo.path) as connection:
        connection.execute(
            """CREATE TRIGGER reject_recommendation BEFORE INSERT
               ON turn_recommendations BEGIN
               SELECT RAISE(ABORT, 'forced recommendation failure');
               END"""
        )

    with pytest.raises(sqlite3.IntegrityError, match="forced recommendation failure"):
        repo.complete_turn(1, conversation.id, turn.id, "回答", ["mug"])

    stored = repo.get_conversation(1, conversation.id).turns[0]
    assert stored.status == "pending"
    assert stored.assistant_content is None
    assert stored.recommended_product_ids == ()


def test_initialize_upgrades_existing_database_without_losing_turns(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE conversations (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, title TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE turns (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL,
                user_content TEXT NOT NULL, assistant_content TEXT,
                status TEXT NOT NULL, error_code TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            );
            INSERT INTO conversations VALUES ('c', 1, 'old', 'now', 'now');
            INSERT INTO turns VALUES ('t', 'c', 'question', 'answer', 'completed', NULL, 'now', 'now');
            """
        )

    repo = ConversationRepository(database)
    repo.initialize()

    stored = repo.get_conversation(1, "c").turns[0]
    assert stored.assistant_content == "answer"
    assert stored.recommended_product_ids == ()


def test_fail_turn_discards_assistant_content(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    conversation = repo.create_conversation(1)
    turn = repo.create_turn(1, conversation.id, "问题")
    with sqlite3.connect(repo.path) as connection:
        connection.execute(
            "UPDATE turns SET assistant_content = ? WHERE id = ?",
            ("不应保留的部分回答", turn.id),
        )
        connection.execute(
            "INSERT INTO turn_recommendations VALUES (?, ?, 0)",
            (turn.id, "stale-product"),
        )

    failed = repo.fail_turn(1, conversation.id, turn.id, "model_stream_failed")

    assert failed.status == "failed"
    assert failed.assistant_content is None
    assert failed.error_code == "model_stream_failed"
    assert repo.get_conversation(1, conversation.id).turns[0].recommended_product_ids == ()


def test_only_latest_failed_turn_can_prepare_retry(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    conversation = repo.create_conversation(1)
    older = repo.create_turn(1, conversation.id, "旧问题")
    repo.fail_turn(1, conversation.id, older.id, "failed")
    latest = repo.create_turn(1, conversation.id, "新问题")
    repo.fail_turn(1, conversation.id, latest.id, "failed")
    with sqlite3.connect(repo.path) as connection:
        connection.execute(
            "INSERT INTO turn_recommendations VALUES (?, ?, 0)",
            (latest.id, "stale-product"),
        )

    with pytest.raises(TurnNotRetryable):
        repo.prepare_retry(1, conversation.id, older.id)

    retried = repo.prepare_retry(1, conversation.id, latest.id)
    assert retried.id == latest.id
    assert retried.status == "pending"
    assert retried.error_code is None
    assert repo.get_conversation(1, conversation.id).turns[-1].recommended_product_ids == ()


def test_model_messages_exclude_failed_turns_and_include_active_user(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    conversation = repo.create_conversation(1)
    completed = repo.create_turn(1, conversation.id, "已完成问题")
    repo.complete_turn(1, conversation.id, completed.id, "已完成回答")
    failed = repo.create_turn(1, conversation.id, "失败问题")
    repo.fail_turn(1, conversation.id, failed.id, "failed")
    active = repo.create_turn(1, conversation.id, "当前问题")

    assert repo.model_messages(1, conversation.id, active.id) == [
        {"role": "user", "content": "已完成问题"},
        {"role": "assistant", "content": "已完成回答"},
        {"role": "user", "content": "当前问题"},
    ]


def test_recover_pending_marks_only_fixed_users_pending_turns_failed(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    first = repo.create_conversation(1)
    second = repo.create_conversation(2)
    first_turn = repo.create_turn(1, first.id, "用户一")
    second_turn = repo.create_turn(2, second.id, "用户二")

    assert repo.recover_pending(1) == 1

    assert repo.get_conversation(1, first.id).turns[0].status == "failed"
    assert repo.get_conversation(1, first.id).turns[0].error_code == "service_restarted"
    assert repo.get_conversation(2, second.id).turns[0] == second_turn
    assert repo.get_conversation(1, first.id).turns[0].id == first_turn.id
