from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any


NUMERIC_SHORT_ANSWER_FORMAT = "integer_0_999"
NUMERIC_SHORT_ANSWER_MIN = 0
NUMERIC_SHORT_ANSWER_MAX = 999
_NUMERIC_SHORT_ANSWER_RE = re.compile(r"^[0-9]{1,3}$")


def normalize_numeric_short_answer(value: Any) -> str | None:
    """Return the canonical decimal representation for an integer in 0..999."""
    if value is None or isinstance(value, bool):
        return None

    text = str(value).strip()
    if not _NUMERIC_SHORT_ANSWER_RE.fullmatch(text):
        return None

    number = int(text)
    if not NUMERIC_SHORT_ANSWER_MIN <= number <= NUMERIC_SHORT_ANSWER_MAX:
        return None
    return str(number)


def numeric_short_answer_matches(student_answer: Any, correct_answer: Any) -> bool:
    student = normalize_numeric_short_answer(student_answer)
    correct = normalize_numeric_short_answer(correct_answer)
    return student is not None and correct is not None and student == correct


def numeric_short_answer_question_ids(
    *,
    question_ids: Iterable[int],
    question_kind: Callable[[int], str | None],
    answers: Mapping[str, Any] | None,
) -> set[int]:
    """Identify essay slots configured with a numeric 0..999 answer key."""
    answer_map = answers if isinstance(answers, Mapping) else {}
    return {
        int(question_id)
        for question_id in question_ids
        if question_kind(int(question_id)) == "essay"
        and normalize_numeric_short_answer(answer_map.get(str(int(question_id)))) is not None
    }


def is_math_subject(value: Any) -> bool:
    normalized = "".join(str(value or "").strip().lower().split())
    return "수학" in normalized or normalized.startswith("math")


def is_math_exam(exam: Any) -> bool:
    if exam is None:
        return False
    if is_math_subject(getattr(exam, "subject", None)):
        return True
    sessions = getattr(exam, "sessions", None)
    if sessions is None:
        return False
    try:
        subjects = sessions.values_list("lecture__subject", flat=True)
    except (AttributeError, TypeError, ValueError):
        return False
    return any(is_math_subject(subject) for subject in subjects)


def math_numeric_short_answer_question_ids(
    *,
    subject: Any = None,
    exam: Any = None,
    question_ids: Iterable[int],
    question_kind: Callable[[int], str | None],
    answers: Any,
) -> set[int]:
    if not (is_math_subject(subject) or is_math_exam(exam)):
        return set()
    return numeric_short_answer_question_ids(
        question_ids=question_ids,
        question_kind=question_kind,
        answers=answers,
    )
