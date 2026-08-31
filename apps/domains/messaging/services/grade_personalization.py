from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable


GRADE_PERSONALIZATION_CODE = "grade_personalization_incomplete"


@dataclass(frozen=True)
class GradePersonalizationIssue:
    code: str = GRADE_PERSONALIZATION_CODE
    title: str = "성적 알림 개인화 확인 필요"
    detail: str = (
        "성적 알림의 학생별 본문이 발송 대상과 정확히 일치하지 않습니다. "
        "미리보기를 새로 생성한 뒤 다시 시도해 주세요."
    )


def normalize_per_student_context(
    raw_per_student: Any,
) -> tuple[dict[int, dict[str, Any]], bool]:
    normalized: dict[int, dict[str, Any]] = {}
    invalid = not isinstance(raw_per_student, dict)
    if invalid:
        return normalized, True

    for raw_student_id, raw_context in raw_per_student.items():
        try:
            student_id = int(raw_student_id)
        except (TypeError, ValueError):
            invalid = True
            continue
        if student_id <= 0 or student_id in normalized or not isinstance(raw_context, dict):
            invalid = True
            continue
        normalized[student_id] = raw_context
    return normalized, invalid


def validate_grade_personalization(
    *,
    block_category: str,
    raw_per_student: Any,
    recipients: Iterable[Any],
) -> tuple[dict[int, dict[str, Any]], GradePersonalizationIssue | None]:
    normalized, invalid = normalize_per_student_context(raw_per_student)
    if block_category.strip() != "grades":
        return normalized, None

    expected_student_ids = {int(recipient.student_id) for recipient in recipients}
    if invalid or set(normalized) != expected_student_ids:
        return normalized, GradePersonalizationIssue()

    for student_id in expected_student_ids:
        body = normalized[student_id].get("_body_subst")
        if not isinstance(body, str) or not body.strip():
            return normalized, GradePersonalizationIssue()

    return normalized, None
