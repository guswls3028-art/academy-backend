"""Shared selected-score arithmetic. Missing/provisional scores are never zeroes."""
from __future__ import annotations

from decimal import Decimal


def assessment_order(session, current_ids, *, key):
    """Keep surviving local positions; append newly linked assessments at the end."""
    available = list(dict.fromkeys(int(value) for value in current_ids))
    allowed = set(available)
    stored = (session.score_totals or {}).get(key) or []
    prior = list(dict.fromkeys(value for value in stored if value in allowed))
    prior_ids = set(prior)
    return [*prior, *(value for value in available if value not in prior_ids)]


def order_assessment_rows(session, rows, *, id_key, order_key):
    rows_by_id = {int(row[id_key]): row for row in rows}
    return [rows_by_id[value] for value in assessment_order(session, rows_by_id, key=order_key)]


def total_config(session, exams, homeworks):
    stored = session.score_totals or {}
    exam_order = assessment_order(session, [row["exam_id"] for row in exams], key="exam_order")
    homework_order = assessment_order(session, [row["homework_id"] for row in homeworks], key="homework_order")
    numeric_homework_ids = {int(row["homework_id"]) for row in homeworks if row.get("grading_mode") != "COMPLETION"}
    return {
        "version": stored.get("version", 0),
        "exam_ids": [value for value in exam_order if stored.get("exam_ids") is None or value in stored["exam_ids"]],
        "homework_ids": [value for value in homework_order if value in numeric_homework_ids and (stored.get("homework_ids") is None or value in stored["homework_ids"])],
        "exam_order": exam_order,
        "homework_order": homework_order,
    }


def selected_total(options, entries, selected_ids, *, id_key):
    selected = set(selected_ids)
    by_id = {int(entry[id_key]): entry for entry in entries}
    score = Decimal("0")
    maximum = Decimal("0")
    selected_count = scored_count = 0
    for option in options:
        item_id = int(option[id_key])
        if item_id not in selected or item_id not in by_id:
            continue  # Not assigned to this student.
        selected_count += 1
        block = by_id[item_id].get("block") or {}
        maximum += Decimal(str(option.get("max_score") or block.get("max_score") or 0))
        pending = (
            block.get("is_provisional") or block.get("grading_status")
            or (block.get("meta") or {}).get("status") == "NOT_SUBMITTED"
        )
        if block.get("score") is not None and not pending:
            scored_count += 1
            score += Decimal(str(block["score"]))
    return {
        "score": float(score) if scored_count else None,
        "max_score": float(maximum),
        "selected_count": selected_count,
        "scored_count": scored_count,
        "complete": selected_count > 0 and selected_count == scored_count,
    }


def row_totals(meta, row):
    config = meta["total_config"]
    return {
        "exam_total": selected_total(meta["exams"], row["exams"], config["exam_ids"], id_key="exam_id"),
        "homework_total": selected_total(meta["homeworks"], row["homeworks"], config["homework_ids"], id_key="homework_id"),
    }
