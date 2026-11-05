from pathlib import Path

import pytest

from agent_service.prompt_loader import PromptLoadError, load_prompt


def test_load_prompt_reads_utf8_text_without_caching(tmp_path: Path) -> None:
    path = tmp_path / "prompt.md"
    path.write_text("第一版 / v1", encoding="utf-8")

    assert load_prompt(path) == "第一版 / v1"
    path.write_text("第二版 / v2", encoding="utf-8")
    assert load_prompt(path) == "第二版 / v2"


@pytest.mark.parametrize("content", ["", "   \n\t"])
def test_load_prompt_rejects_empty_content(tmp_path: Path, content: str) -> None:
    path = tmp_path / "empty.md"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(PromptLoadError, match="empty"):
        load_prompt(path)


def test_load_prompt_rejects_missing_invalid_utf8_and_oversized_files(
    tmp_path: Path,
) -> None:
    with pytest.raises(PromptLoadError, match="not found"):
        load_prompt(tmp_path / "missing.md")

    invalid = tmp_path / "invalid.md"
    invalid.write_bytes(b"\xff")
    with pytest.raises(PromptLoadError, match="UTF-8"):
        load_prompt(invalid)

    oversized = tmp_path / "oversized.md"
    oversized.write_text("x" * (1024 * 1024 + 1), encoding="utf-8")
    with pytest.raises(PromptLoadError, match="1 MiB"):
        load_prompt(oversized)
