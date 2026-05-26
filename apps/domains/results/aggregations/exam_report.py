from __future__ import annotations

from typing import Any, Iterable

from apps.domains.results.models import ResultItem


def empty_result_item_analysis() -> dict[str, Any]:
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
    return {
        "total_questions": total,
        "correct_count": correct,
        "wrong_count": wrong,
        "accuracy_rate": round((correct / total) * 100, 1) if total else None,
        "wrong_question_numbers": wrong_numbers,
    }


def build_result_item_analysis_map(result_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
    """
    Batch result_id -> item analysis map for student grade summaries.
    """
    ids = [int(result_id) for result_id in result_ids if result_id]
    if not ids:
        return {}

    grouped: dict[int, list[dict[str, Any]]] = {result_id: [] for result_id in ids}
    rows = (
        ResultItem.objects
        .filter(result_id__in=ids)
        .select_related("question")
        .order_by("result_id", "question__number", "question_id")
        .values("result_id", "question_id", "question__number", "is_correct")
    )
    for row in rows:
        result_id = int(row["result_id"])
        grouped.setdefault(result_id, []).append({
            "question_id": row["question_id"],
            "question_number": row["question__number"] or row["question_id"],
            "is_correct": row["is_correct"],
        })

    return {
        result_id: summarize_result_items(items)
        for result_id, items in grouped.items()
    }
