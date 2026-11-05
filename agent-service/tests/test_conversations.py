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
    assert {"conversations", "turns"} <= tables
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

    completed = repo.complete_turn(1, conversation.id, turn.id, "完整回答")

    assert completed.status == "completed"
    assert completed.assistant_content == "完整回答"
    stored = repo.get_conversation(1, conversation.id).turns[0]
    assert stored == completed
    assert repo.list_conversations(1)[0].last_turn_status == "completed"


def test_fail_turn_discards_assistant_content(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    conversation = repo.create_conversation(1)
    turn = repo.create_turn(1, conversation.id, "问题")
    with sqlite3.connect(repo.path) as connection:
        connection.execute(
            "UPDATE turns SET assistant_content = ? WHERE id = ?",
            ("不应保留的部分回答", turn.id),
        )

    failed = repo.fail_turn(1, conversation.id, turn.id, "model_stream_failed")

    assert failed.status == "failed"
    assert failed.assistant_content is None
    assert failed.error_code == "model_stream_failed"


def test_only_latest_failed_turn_can_prepare_retry(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    conversation = repo.create_conversation(1)
    older = repo.create_turn(1, conversation.id, "旧问题")
    repo.fail_turn(1, conversation.id, older.id, "failed")
    latest = repo.create_turn(1, conversation.id, "新问题")
    repo.fail_turn(1, conversation.id, latest.id, "failed")

    with pytest.raises(TurnNotRetryable):
        repo.prepare_retry(1, conversation.id, older.id)

    retried = repo.prepare_retry(1, conversation.id, latest.id)
    assert retried.id == latest.id
    assert retried.status == "pending"
    assert retried.error_code is None


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
