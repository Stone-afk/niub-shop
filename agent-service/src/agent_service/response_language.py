"""Small deterministic language selector for user-facing Agent responses."""

from __future__ import annotations

import re
from typing import Literal


ResponseLanguage = Literal["zh", "en"]
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def detect_language(text: str, previous: ResponseLanguage | None = None) -> ResponseLanguage:
    chinese = len(_CJK_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    if chinese and latin and previous:
        return previous
    if chinese > latin:
        return "zh"
    if latin > chinese:
        return "en"
    return previous or "zh"


def language_instruction(language: ResponseLanguage) -> str:
    if language == "en":
        return (
            "[response_language=en]\n"
            "Reply only in English. Do not add Chinese translation or bilingual duplicate text."
        )
    return (
        "[response_language=zh]\n"
        "只使用中文回答。除非用户明确要求翻译，不要附带英文翻译或中英双语重复内容。"
    )
