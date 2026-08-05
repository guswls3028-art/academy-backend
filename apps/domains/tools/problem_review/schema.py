from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "problem-review-report/v1"
DIFFICULTIES = ("검수 필요", "하", "중", "중상", "상", "최상")
MAX_QUESTIONS = 80


def _text(value: Any, *, limit: int = 1200) -> str:
    return str(value or "").strip()[:limit]


def _int(value: Any, *, default: int = 0, minimum: int = 0, maximum: int = 10_000) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _string_list(value: Any, *, limit: int = 12, item_limit: int = 400) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value[:limit] if (text := _text(item, limit=item_limit))]


def _dict_list(value: Any, *, limit: int) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value[:limit] if isinstance(item, dict)]


def _metadata(value: Any, fallback: dict[str, Any]) -> dict[str, str]:
    raw = value if isinstance(value, dict) else {}
    base = fallback.get("metadata") if isinstance(fallback.get("metadata"), dict) else {}
    fields = {
        "title": 200,
        "school": 120,
        "subject": 100,
        "grade": 40,
        "exam_name": 120,
        "exam_date": 30,
        "duration": 40,
        "total_score": 40,
        "instructor_name": 80,
        "audience": 80,
    }
    return {
        key: _text(raw.get(key) if raw.get(key) not in (None, "") else base.get(key), limit=limit)
        for key, limit in fields.items()
    }


def _question_number(value: Any, fallback: int) -> int:
    return _int(value, default=fallback, minimum=1, maximum=999)


def _source_key(item: dict[str, Any], fallback: int) -> int:
    return _int(item.get("source_number"), default=0, minimum=0, maximum=999) or _question_number(
        item.get("number"),
        fallback,
    )


def _questions(
    value: Any,
    fallback: dict[str, Any],
    *,
    preserve_question_set: bool,
) -> list[dict[str, Any]]:
    base_items = _dict_list(fallback.get("questions"), limit=MAX_QUESTIONS)
    base_by_source = {
        _source_key(item, index + 1): item
        for index, item in enumerate(base_items)
    }
    raw_items = _dict_list(value, limit=MAX_QUESTIONS)
    if preserve_question_set:
        raw_by_source = {
            _source_key(item, index + 1): item
            for index, item in enumerate(raw_items)
        }
        source_numbers = list(base_by_source)
        source_numbers.extend(
            source_number
            for source_number in raw_by_source
            if source_number not in source_numbers
        )
        entries = [
            (source_number, base_by_source.get(source_number, {}), raw_by_source.get(source_number, {}))
            for source_number in source_numbers[:MAX_QUESTIONS]
        ]
    else:
        entries = []
        for index, raw in enumerate(raw_items):
            explicit_source = _int(raw.get("source_number"), default=0, minimum=0, maximum=999)
            source_number = explicit_source or _question_number(raw.get("number"), index + 1)
            entries.append((source_number, base_by_source.get(explicit_source, {}) if explicit_source else {}, raw))
    output: list[dict[str, Any]] = []
    for source_number, base, raw in entries:
        number = _question_number(raw.get("number") or base.get("number"), source_number)
        difficulty = _text(raw.get("difficulty") or base.get("difficulty") or "검수 필요", limit=10)
        if difficulty not in DIFFICULTIES:
            difficulty = "검수 필요"
        output.append({
            "number": number,
            "source_number": _int(
                raw.get("source_number") or base.get("source_number"),
                default=source_number if base else 0,
                minimum=0,
                maximum=999,
            ),
            "unit": _text(raw.get("unit") or base.get("unit"), limit=120),
            "answer": _text(raw.get("answer") or base.get("answer"), limit=160),
            "points": _text(raw.get("points") or base.get("points"), limit=30),
            "difficulty": difficulty,
            "key_point": _text(raw.get("key_point") or base.get("key_point"), limit=900),
            "trap": _text(raw.get("trap") or base.get("trap"), limit=700),
            "validity": _text(raw.get("validity") or base.get("validity"), limit=500),
            "review_note": _text(raw.get("review_note") or base.get("review_note"), limit=700),
            "source_excerpt": _text(
                base.get("source_excerpt") if base else raw.get("source_excerpt"),
                limit=260,
            ),
            "confidence": (
                _text(raw.get("confidence") or base.get("confidence") or "low", limit=10)
                if _text(raw.get("confidence") or base.get("confidence") or "low", limit=10) in {"high", "medium", "low"}
                else "low"
            ),
        })
    return output


def normalize_report_payload(
    value: Any,
    *,
    fallback: dict[str, Any] | None = None,
    preserve_question_set: bool = True,
) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    base = fallback if isinstance(fallback, dict) else {}
    summary_raw = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
    summary_base = base.get("summary") if isinstance(base.get("summary"), dict) else {}
    difficulty_raw = raw.get("difficulty") if isinstance(raw.get("difficulty"), dict) else {}
    difficulty_base = base.get("difficulty") if isinstance(base.get("difficulty"), dict) else {}
    guidance_raw = raw.get("parent_guidance") if isinstance(raw.get("parent_guidance"), dict) else {}
    guidance_base = base.get("parent_guidance") if isinstance(base.get("parent_guidance"), dict) else {}
    conclusion_raw = raw.get("conclusion") if isinstance(raw.get("conclusion"), dict) else {}
    conclusion_base = base.get("conclusion") if isinstance(base.get("conclusion"), dict) else {}

    axes = [
        {
            "title": _text(item.get("title"), limit=120),
            "description": _text(item.get("description"), limit=700),
        }
        for item in _dict_list(raw.get("assessment_axes") or base.get("assessment_axes"), limit=6)
    ]
    domains = [
        {
            "name": _text(item.get("name"), limit=120),
            "question_numbers": _string_list(item.get("question_numbers"), limit=30, item_limit=16),
            "points": _text(item.get("points"), limit=30),
            "ratio": _text(item.get("ratio"), limit=30),
            "insight": _text(item.get("insight"), limit=700),
        }
        for item in _dict_list(raw.get("domains") or base.get("domains"), limit=12)
    ]
    distributions = [
        {
            "label": (
                label if (label := _text(item.get("label"), limit=10)) in DIFFICULTIES else "검수 필요"
            ),
            "question_numbers": _string_list(item.get("question_numbers"), limit=40, item_limit=16),
            "points": _text(item.get("points"), limit=30),
            "note": _text(item.get("note"), limit=300),
        }
        for item in _dict_list(difficulty_raw.get("distribution") or difficulty_base.get("distribution"), limit=6)
    ]
    key_items = [
        {
            "rank": _int(item.get("rank"), default=index + 1, minimum=1, maximum=20),
            "title": _text(item.get("title"), limit=180),
            "question_numbers": _string_list(item.get("question_numbers"), limit=12, item_limit=16),
            "reason": _text(item.get("reason"), limit=900),
            "collapse_point": _text(item.get("collapse_point"), limit=800),
            "prescription": _text(item.get("prescription"), limit=800),
        }
        for index, item in enumerate(_dict_list(raw.get("key_items") or base.get("key_items"), limit=8))
    ]
    patterns = [
        {
            "title": _text(item.get("title"), limit=140),
            "symptom": _text(item.get("symptom"), limit=600),
            "cause": _text(item.get("cause"), limit=600),
            "prescription": _text(item.get("prescription"), limit=700),
        }
        for item in _dict_list(raw.get("failure_patterns") or base.get("failure_patterns"), limit=8)
    ]

    questions = _questions(
        raw.get("questions"),
        base,
        preserve_question_set=preserve_question_set,
    )
    distribution_details = {item["label"]: item for item in distributions}
    grouped_questions: dict[str, list[str]] = {}
    for question in questions:
        label = question.get("difficulty") or "검수 필요"
        grouped_questions.setdefault(label, []).append(str(question["number"]))
    distributions = [
        {
            "label": label,
            "question_numbers": grouped_questions[label],
            "points": distribution_details.get(label, {}).get("points", ""),
            "note": distribution_details.get(label, {}).get("note", ""),
        }
        for label in DIFFICULTIES
        if grouped_questions.get(label)
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "metadata": _metadata(raw.get("metadata"), base),
        "summary": {
            "one_line": _text(summary_raw.get("one_line") or summary_base.get("one_line"), limit=500),
            "character": _text(summary_raw.get("character") or summary_base.get("character"), limit=1000),
            "total_questions": len(questions),
            "total_points": _text(summary_raw.get("total_points") or summary_base.get("total_points"), limit=40),
            "student_burden": _text(summary_raw.get("student_burden") or summary_base.get("student_burden"), limit=700),
        },
        "assessment_axes": axes,
        "domains": domains,
        "difficulty": {
            "distribution": distributions,
            "grade_estimate_note": _text(
                difficulty_raw.get("grade_estimate_note") or difficulty_base.get("grade_estimate_note"),
                limit=700,
            ),
        },
        "questions": questions,
        "key_items": key_items,
        "failure_patterns": patterns,
        "parent_guidance": {
            "avoid": _string_list(guidance_raw.get("avoid") or guidance_base.get("avoid"), limit=8),
            "recommended": _string_list(
                guidance_raw.get("recommended") or guidance_base.get("recommended"),
                limit=8,
            ),
        },
        "conclusion": {
            "headline": _text(conclusion_raw.get("headline") or conclusion_base.get("headline"), limit=240),
            "actions": _string_list(conclusion_raw.get("actions") or conclusion_base.get("actions"), limit=8),
        },
        "warnings": _string_list(raw.get("warnings") or base.get("warnings"), limit=20, item_limit=500),
    }


def build_source_draft(
    *,
    metadata: dict[str, Any],
    questions: list[dict[str, Any]],
    warnings: list[str],
) -> dict[str, Any]:
    fallback_questions = []
    for index, item in enumerate(questions[:MAX_QUESTIONS], start=1):
        fallback_questions.append({
            "number": _question_number(item.get("number"), index),
            "source_number": _question_number(item.get("number"), index),
            "unit": _text(item.get("unit"), limit=120),
            "answer": _text(item.get("answer"), limit=160),
            "points": _text(item.get("points"), limit=30),
            "difficulty": "검수 필요",
            "key_point": "",
            "trap": "",
            "validity": "",
            "review_note": "",
            "source_excerpt": _text(item.get("prompt"), limit=260),
            "confidence": _text(item.get("confidence") or "low", limit=10),
        })
    return normalize_report_payload({
        "metadata": metadata,
        "summary": {"total_questions": len(fallback_questions)},
        "questions": fallback_questions,
        "warnings": warnings,
    })
