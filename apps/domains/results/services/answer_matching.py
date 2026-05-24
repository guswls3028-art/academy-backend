from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


_CIRCLED_DIGITS = str.maketrans({
    "①": "1",
    "②": "2",
    "③": "3",
    "④": "4",
    "⑤": "5",
    "⑥": "6",
    "⑦": "7",
    "⑧": "8",
    "⑨": "9",
})
_MULTI_ANSWER_SEPARATOR_RE = re.compile(r"\s*(?:[,;|]|또는|혹은|\bor\b)\s*", re.IGNORECASE)
_OBJECTIVE_TOKEN_RE = re.compile(r"^[0-9A-Z]+$")


def normalize_answer(value: Any) -> str:
    return str(value or "").strip().upper().translate(_CIRCLED_DIGITS)


def format_answer_for_display(value: Any) -> str:
    if isinstance(value, str) or not isinstance(value, Iterable):
        return str(value or "").strip()
    return ",".join(str(v or "").strip() for v in value if str(v or "").strip())


def correct_answer_candidates(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str) or not isinstance(value, Iterable):
        text = normalize_answer(value)
        if not text:
            return []
        raw_parts = _MULTI_ANSWER_SEPARATOR_RE.split(text)
    else:
        raw_parts = [normalize_answer(v) for v in value]

    parts = [p for p in (normalize_answer(part) for part in raw_parts) if p]
    if len(parts) <= 1:
        return parts

    # Delimited strings are treated as multiple correct answers only for
    # objective-style tokens. This avoids turning free-text answers that contain
    # punctuation into accidental partial matches.
    if not all(_OBJECTIVE_TOKEN_RE.fullmatch(part) for part in parts):
        if isinstance(value, str):
            return [normalize_answer(value)]
        return parts

    unique: list[str] = []
    for part in parts:
        if part not in unique:
            unique.append(part)
    return unique


def answer_matches(student_answer: Any, correct_answer: Any) -> bool:
    student = normalize_answer(student_answer)
    if not student:
        return False

    candidates = correct_answer_candidates(correct_answer)
    if not candidates:
        return False

    return student in candidates
