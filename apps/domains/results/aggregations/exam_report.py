from __future__ import annotations

from typing import Any, Iterable


def _empty_result_item_analysis() -> dict[str, Any]:
    return {
        "total_questions": 0,
        "correct_count": 0,
        "wrong_count": 0,
        "accuracy_rate": None,
        "wrong_question_numbers": [],
    }


def summarize_result_items(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """
    Student-facing read model for a result's item-level analysis.

    Correct answers can remain hidden by policy; this summary only exposes
    correctness and question numbers for the student's own result.
    """
    total = 0
    correct = 0
    wrong_numbers: list[int] = []

    for item in items:
        total += 1
        is_correct = bool(item.get("is_correct"))
        if is_correct:
            correct += 1
            continue

        raw_number = item.get("question_number") or item.get("question_id")
        try:
            wrong_numbers.append(int(raw_number))
        except (TypeError, ValueError):
            continue

    wrong = max(total - correct, 0)
    wrong_numbers.sort()
    if not total:
        return _empty_result_item_analysis()

    return {
        "total_questions": total,
        "correct_count": correct,
        "wrong_count": wrong,
        "accuracy_rate": round((correct / total) * 100, 1),
        "wrong_question_numbers": wrong_numbers,
    }
