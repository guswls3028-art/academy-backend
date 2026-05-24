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
_ALTERNATIVE_SEPARATOR_RE = re.compile(r"\s*(?:[|]|또는|혹은|\bor\b)\s*", re.IGNORECASE)
_REQUIRED_SET_SEPARATOR_RE = re.compile(r"\s*(?:[,;&+])\s*")
_OBJECTIVE_TOKEN_RE = re.compile(r"^[0-9A-Z]+$")


def normalize_answer(value: Any) -> str:
    return str(value or "").strip().upper().translate(_CIRCLED_DIGITS)


def format_answer_for_display(value: Any) -> str:
    if isinstance(value, str) or not isinstance(value, Iterable):
        return str(value or "").strip()
    return ",".join(str(v or "").strip() for v in value if str(v or "").strip())


def _objective_tokens_from_required_set(value: Any) -> list[str]:
    if isinstance(value, str) or not isinstance(value, Iterable):
        raw_parts = _REQUIRED_SET_SEPARATOR_RE.split(normalize_answer(value))
    else:
        raw_parts = [normalize_answer(v) for v in value]

    parts = [p for p in (normalize_answer(part) for part in raw_parts) if p]
    if not all(_OBJECTIVE_TOKEN_RE.fullmatch(part) for part in parts):
        return [normalize_answer(value)] if isinstance(value, str) else parts

    unique: list[str] = []
    for part in parts:
        if part not in unique:
            unique.append(part)
    return unique


def correct_answer_candidates(value: Any) -> list[str]:
    """
    Backward-compatible display/debug helper.

    `answer_matches` is set-based: comma/semicolon/+ mean required simultaneous
    marks, while `|`/또는/or mean alternatives. This helper flattens candidates
    for older callers that only need labels.
    """
    if value is None:
        return []

    unique: list[str] = []
    for option in correct_answer_sets(value):
        for token in option:
            if token not in unique:
                unique.append(token)
    return unique


def correct_answer_sets(value: Any) -> list[frozenset[str]]:
    """
    Return acceptable objective answer sets.

    Examples:
    - "1,3" or ["1", "3"] => [{"1", "3"}]  # both must be marked
    - "1|3" / "1 또는 3"   => [{"1"}, {"3"}] # either one is accepted
    - "1,3|2,4"            => [{"1","3"}, {"2","4"}]
    """
    if value is None:
        return []

    if isinstance(value, str):
        text = normalize_answer(value)
        if not text:
            return []
        alternatives = _ALTERNATIVE_SEPARATOR_RE.split(text)
    elif isinstance(value, Iterable):
        alternatives = [value]
    else:
        alternatives = [value]

    out: list[frozenset[str]] = []
    for alt in alternatives:
        tokens = _objective_tokens_from_required_set(alt)
        if not tokens:
            continue
        option = frozenset(tokens)
        if option not in out:
            out.append(option)
    return out


def student_answer_set(value: Any) -> frozenset[str]:
    tokens = _objective_tokens_from_required_set(value)
    return frozenset(tokens)


def answer_matches(student_answer: Any, correct_answer: Any) -> bool:
    student = student_answer_set(student_answer)
    if not student:
        return False

    candidates = correct_answer_sets(correct_answer)
    if not candidates:
        return False

    return student in candidates
