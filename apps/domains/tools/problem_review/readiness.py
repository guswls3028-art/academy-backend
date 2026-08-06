from __future__ import annotations

import hashlib
import json
from typing import Any

from .schema import normalize_report_payload


PLACEHOLDER_VALUES = {"", "-", "미확인", "확인 필요", "검수 필요"}


def _has_text(value: Any) -> bool:
    return str(value or "").strip() not in PLACEHOLDER_VALUES


def report_fingerprint(draft: dict[str, Any]) -> tuple[dict[str, Any], str]:
    snapshot = normalize_report_payload(
        draft,
        preserve_question_set=False,
        preserve_review_status=True,
    )
    canonical = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return snapshot, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _question_issues(question: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    for key, label in (
        ("unit", "단원"),
        ("answer", "정답·정답 예시"),
        ("key_point", "핵심 포인트"),
        ("trap", "오답 함정"),
        ("validity", "타당성 메모"),
    ):
        if not _has_text(question.get(key)):
            issues.append(label)
    if question.get("difficulty") == "검수 필요":
        issues.append("난이도")
    if question.get("thinking_action") == "검수 필요":
        issues.append("사고행동")
    if question.get("review_status") != "verified":
        issues.append("원문·정답 대조")
    return issues


def build_review_readiness(
    draft: dict[str, Any],
    *,
    finalized_fingerprint: str = "",
    finalized_at: Any = None,
) -> dict[str, Any]:
    normalized, fingerprint = report_fingerprint(draft)
    questions = normalized.get("questions") or []
    question_checks = []
    for index, question in enumerate(questions):
        issues = _question_issues(question)
        question_checks.append({
            "index": index,
            "number": question.get("number"),
            "ready": not issues,
            "issues": issues,
        })

    summary = normalized.get("summary") or {}
    metadata = normalized.get("metadata") or {}
    axes = normalized.get("assessment_axes") or []
    key_items = normalized.get("key_items") or []
    patterns = normalized.get("failure_patterns") or []
    protocol = normalized.get("recovery_protocol") or {}
    conclusion = normalized.get("conclusion") or {}
    section_checks = [
        {
            "key": "metadata",
            "label": "시험 기본 정보",
            "ready": all(_has_text(metadata.get(key)) for key in ("title", "school", "subject", "exam_name")),
        },
        {
            "key": "summary",
            "label": "총평",
            "ready": _has_text(summary.get("one_line")) and _has_text(summary.get("character")),
        },
        {
            "key": "axes",
            "label": "출제 기조",
            "ready": len([item for item in axes if _has_text(item.get("title")) and _has_text(item.get("description"))]) >= 2,
        },
        {
            "key": "questions",
            "label": "전 문항 원문·정답 대조",
            "ready": bool(question_checks) and all(item["ready"] for item in question_checks),
        },
        {
            "key": "key_items",
            "label": "핵심 변별",
            "ready": any(
                _has_text(item.get("title"))
                and bool(item.get("question_numbers"))
                and _has_text(item.get("evidence"))
                and _has_text(item.get("prescription"))
                for item in key_items
            ),
        },
        {
            "key": "failure_patterns",
            "label": "실패 패턴",
            "ready": any(
                all(_has_text(item.get(key)) for key in ("title", "symptom", "cause", "prescription"))
                for item in patterns
            ),
        },
        {
            "key": "recovery",
            "label": "회복 계획",
            "ready": all(bool(protocol.get(key)) for key in ("within_72_hours", "within_two_weeks", "next_exam")),
        },
        {
            "key": "conclusion",
            "label": "결론·다음 행동",
            "ready": _has_text(conclusion.get("headline")) and any(_has_text(item) for item in conclusion.get("actions") or []),
        },
    ]
    verified_questions = sum(1 for item in question_checks if item["ready"])
    ready_for_finalize = all(item["ready"] for item in section_checks)
    is_finalized = bool(
        ready_for_finalize
        and finalized_at
        and finalized_fingerprint
        and finalized_fingerprint == fingerprint
    )
    completed_units = verified_questions + sum(1 for item in section_checks if item["ready"])
    total_units = len(question_checks) + len(section_checks)
    return {
        "ready_for_finalize": ready_for_finalize,
        "is_finalized": is_finalized,
        "fingerprint": fingerprint,
        "finalized_at": finalized_at.isoformat() if hasattr(finalized_at, "isoformat") else finalized_at,
        "total_questions": len(question_checks),
        "verified_questions": verified_questions,
        "unresolved_questions": len(question_checks) - verified_questions,
        "progress_percent": round((completed_units / total_units) * 100) if total_units else 0,
        "sections": section_checks,
        "questions": question_checks,
    }
