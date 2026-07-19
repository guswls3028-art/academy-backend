"""Cross-domain workflow for student-submitted school and mock-exam scores."""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from math import isfinite
from typing import Any, Mapping

from django.db import transaction
from django.utils import timezone

from apps.domains.results.models import StudentReportedScore
from apps.domains.students.models import Student


SCHOOL_SOURCES = {StudentReportedScore.Source.SCHOOL_EXAM}
MOCK_SOURCES = {
    StudentReportedScore.Source.NATIONAL_MOCK,
    StudentReportedScore.Source.KICE_MOCK,
}


def _required_text(payload: Mapping[str, Any], key: str, *, max_length: int) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} required")
    if len(value) > max_length:
        raise ValueError(f"{key} too long")
    return value


def _integer(
    payload: Mapping[str, Any],
    key: str,
    *,
    minimum: int,
    maximum: int,
    required: bool = True,
) -> int | None:
    raw = payload.get(key)
    if raw in (None, ""):
        if required:
            raise ValueError(f"{key} required")
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be integer") from None
    if value < minimum or value > maximum:
        raise ValueError(f"{key} must be between {minimum} and {maximum}")
    return value


def _decimal(
    payload: Mapping[str, Any],
    key: str,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
    required: bool = True,
) -> Decimal | None:
    raw = payload.get(key)
    if raw in (None, ""):
        if required:
            raise ValueError(f"{key} required")
        return None
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{key} must be number") from None
    if not isfinite(float(value)):
        raise ValueError(f"{key} must be finite")
    if minimum is not None and value < minimum:
        raise ValueError(f"{key} is below minimum")
    if maximum is not None and value > maximum:
        raise ValueError(f"{key} is above maximum")
    return value


def _date(payload: Mapping[str, Any], key: str) -> date | None:
    raw = str(payload.get(key) or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"{key} must be YYYY-MM-DD") from None


def validate_student_score_submission(
    *,
    tenant: Any,
    user: Any,
    student_ps: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate before R2 upload so invalid score metadata causes no storage write."""
    student = Student.objects.filter(
        tenant=tenant,
        user=user,
        ps_number=student_ps,
        deleted_at__isnull=True,
    ).first()
    if not student:
        raise ValueError("학생 본인만 자기 성적표를 제출할 수 있습니다.")

    source = str(payload.get("score_source") or "").strip()
    allowed_sources = {choice for choice, _label in StudentReportedScore.Source.choices}
    if source not in allowed_sources:
        raise ValueError("score_source invalid")

    academic_year = _integer(payload, "academic_year", minimum=2000, maximum=2100)
    subject = _required_text(payload, "subject", max_length=50)
    score = _decimal(payload, "score", minimum=Decimal("0"))
    max_score = _decimal(payload, "max_score", minimum=Decimal("0.01"))
    if score is not None and max_score is not None and score > max_score:
        raise ValueError("score cannot exceed max_score")

    standard_score = _decimal(payload, "standard_score", minimum=Decimal("0"), required=False)
    percentile = _decimal(
        payload,
        "percentile",
        minimum=Decimal("0"),
        maximum=Decimal("100"),
        required=False,
    )
    grade_rank = _integer(payload, "grade_rank", minimum=1, maximum=9, required=False)
    grade_scale = str(payload.get("grade_scale") or "").strip()
    if grade_scale and grade_scale not in {choice for choice, _label in StudentReportedScore.GradeScale.choices}:
        raise ValueError("grade_scale invalid")
    if grade_rank is not None and not grade_scale:
        raise ValueError("등급을 입력할 때는 5등급제 또는 9등급제를 선택해야 합니다.")
    if grade_rank is not None and grade_scale == StudentReportedScore.GradeScale.FIVE and grade_rank > 5:
        raise ValueError("5등급제 등급은 1~5여야 합니다.")
    achievement_level = str(payload.get("achievement_level") or "").strip().upper()
    if achievement_level and achievement_level not in {"A", "B", "C", "D", "E"}:
        raise ValueError("achievement_level must be A to E")
    subject_average = _decimal(payload, "subject_average", minimum=Decimal("0"), required=False)
    standard_deviation = _decimal(payload, "standard_deviation", minimum=Decimal("0"), required=False)
    cohort_size = _integer(payload, "cohort_size", minimum=1, maximum=100000, required=False)
    exam_date = _date(payload, "exam_date")

    semester = None
    exam_round = ""
    exam_month = None
    if source in SCHOOL_SOURCES:
        semester = _integer(payload, "semester", minimum=1, maximum=2)
        exam_round = str(payload.get("exam_round") or "").strip()
        if exam_round not in {choice for choice, _label in StudentReportedScore.ExamRound.choices}:
            raise ValueError("exam_round invalid")
    else:
        exam_month = _integer(payload, "exam_month", minimum=1, maximum=12)
        if source == StudentReportedScore.Source.KICE_MOCK and exam_month not in (6, 9):
            raise ValueError("평가원 수능 모의평가는 6월 또는 9월이어야 합니다.")
        if achievement_level or subject_average is not None or standard_deviation is not None or cohort_size is not None:
            raise ValueError("학교 성적 지표는 학교 내신에만 입력할 수 있습니다.")

    return {
        "tenant": tenant,
        "student": student,
        "submitted_by": user,
        "source": source,
        "academic_year": academic_year,
        "semester": semester,
        "exam_round": exam_round,
        "exam_month": exam_month,
        "exam_date": exam_date,
        "subject": subject,
        "score": score,
        "max_score": max_score,
        "standard_score": standard_score,
        "percentile": percentile,
        "grade_rank": grade_rank,
        "grade_scale": grade_scale,
        "achievement_level": achievement_level,
        "subject_average": subject_average,
        "standard_deviation": standard_deviation,
        "cohort_size": cohort_size,
    }


def create_student_score_submission(*, evidence_file: Any, validated: Mapping[str, Any]) -> StudentReportedScore:
    tenant = validated.get("tenant")
    student = validated.get("student")
    submitted_by = validated.get("submitted_by")
    if (
        not tenant
        or not student
        or evidence_file.tenant_id != tenant.id
        or student.tenant_id != tenant.id
        or evidence_file.scope != "student"
        or evidence_file.student_ps != student.ps_number
        or submitted_by is None
        or student.user_id != submitted_by.id
    ):
        raise ValueError("성적표 원본과 학생·학원 정보가 일치하지 않습니다.")
    return StudentReportedScore.objects.create(
        evidence_file=evidence_file,
        **dict(validated),
    )


def score_pct(row: StudentReportedScore | Mapping[str, Any]) -> float | None:
    score = row.score if isinstance(row, StudentReportedScore) else row.get("score")
    max_score = row.max_score if isinstance(row, StudentReportedScore) else row.get("max_score")
    try:
        score_value = float(score)
        max_value = float(max_score)
    except (TypeError, ValueError, OverflowError):
        return None
    if not isfinite(score_value) or not isfinite(max_value) or score_value < 0 or max_value <= 0:
        return None
    return round((score_value / max_value) * 100, 1)


def reported_score_label(row: StudentReportedScore | Mapping[str, Any]) -> str:
    get = (lambda key: getattr(row, key)) if isinstance(row, StudentReportedScore) else row.get
    year = get("academic_year")
    source = get("source")
    if source == StudentReportedScore.Source.SCHOOL_EXAM:
        semester = get("semester")
        exam_round = get("exam_round")
        round_label = "1차 지필평가(중간)" if exam_round == StudentReportedScore.ExamRound.FIRST else "2차 지필평가(기말)"
        return f"{year}년 {semester}학기 {round_label}"
    month = get("exam_month")
    if source == StudentReportedScore.Source.KICE_MOCK:
        return f"{year}년 {month}월 평가원 모의평가"
    return f"{year}년 {month}월 전국연합학력평가"


def serialize_reported_score(row: StudentReportedScore | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(row, StudentReportedScore):
        values = {
            "id": row.id,
            "student_id": row.student_id,
            "source": row.source,
            "academic_year": row.academic_year,
            "semester": row.semester,
            "exam_round": row.exam_round,
            "exam_month": row.exam_month,
            "exam_date": row.exam_date,
            "subject": row.subject,
            "score": row.score,
            "max_score": row.max_score,
            "standard_score": row.standard_score,
            "percentile": row.percentile,
            "grade_rank": row.grade_rank,
            "grade_scale": row.grade_scale,
            "achievement_level": row.achievement_level,
            "subject_average": row.subject_average,
            "standard_deviation": row.standard_deviation,
            "cohort_size": row.cohort_size,
            "status": row.status,
            "review_note": row.review_note,
            "evidence_file_id": row.evidence_file_id,
            "evidence_r2_key": row.evidence_file.r2_key,
            "created_at": row.created_at,
            "reviewed_at": row.reviewed_at,
        }
    else:
        values = dict(row)
    return {
        "id": int(values["id"]),
        "student_id": int(values["student_id"]),
        "source": values["source"],
        "source_group": "school" if values["source"] in SCHOOL_SOURCES else "mock",
        "label": reported_score_label(values),
        "academic_year": int(values["academic_year"]),
        "semester": values.get("semester"),
        "exam_round": values.get("exam_round") or None,
        "exam_month": values.get("exam_month"),
        "exam_date": values.get("exam_date").isoformat() if values.get("exam_date") else None,
        "subject": values["subject"],
        "score": float(values["score"]),
        "max_score": float(values["max_score"]),
        "score_pct": score_pct(values),
        "standard_score": float(values["standard_score"]) if values.get("standard_score") is not None else None,
        "percentile": float(values["percentile"]) if values.get("percentile") is not None else None,
        "grade_rank": values.get("grade_rank"),
        "grade_scale": values.get("grade_scale") or None,
        "achievement_level": values.get("achievement_level") or None,
        "subject_average": float(values["subject_average"]) if values.get("subject_average") is not None else None,
        "standard_deviation": float(values["standard_deviation"]) if values.get("standard_deviation") is not None else None,
        "cohort_size": values.get("cohort_size"),
        "status": values["status"],
        "review_note": values.get("review_note") or "",
        "evidence_file_id": int(values["evidence_file_id"]),
        "evidence_r2_key": values.get("evidence_r2_key") or "",
        "created_at": values.get("created_at").isoformat() if values.get("created_at") else None,
        "reviewed_at": values.get("reviewed_at").isoformat() if values.get("reviewed_at") else None,
    }


def score_submission_map_for_inventory_files(*, tenant: Any, file_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not file_ids:
        return {}
    rows = StudentReportedScore.objects.filter(
        tenant=tenant,
        evidence_file_id__in=file_ids,
    ).select_related("evidence_file")
    return {row.evidence_file_id: serialize_reported_score(row) for row in rows}


def inventory_file_has_reported_score(*, tenant: Any, file_id: int) -> bool:
    return StudentReportedScore.objects.filter(tenant=tenant, evidence_file_id=file_id).exists()


def inventory_files_have_reported_score(*, tenant: Any, file_ids: list[int]) -> bool:
    if not file_ids:
        return False
    return StudentReportedScore.objects.filter(
        tenant=tenant,
        evidence_file_id__in=file_ids,
    ).exists()


@transaction.atomic
def review_student_score(
    *,
    tenant: Any,
    score_id: int,
    action: str,
    reviewed_by: Any,
    review_note: str = "",
) -> StudentReportedScore | None:
    if action not in ("verify", "reject"):
        raise ValueError("action must be verify or reject")

    student_id = StudentReportedScore.objects.filter(
        tenant=tenant,
        id=score_id,
    ).values_list("student_id", flat=True).first()
    if student_id is None:
        return None

    # 모든 검수는 학생 행 → 성적 행 순서로 잠근다. 같은 학생의 서로 다른
    # 정정본을 동시에 처리해도 중복 승인과 교착을 함께 방지한다.
    Student.objects.select_for_update().filter(
        tenant=tenant,
        id=student_id,
    ).exists()
    row = (
        StudentReportedScore.objects.select_for_update()
        .select_related("evidence_file")
        .filter(tenant=tenant, id=score_id)
        .first()
    )
    if not row:
        return None

    now = timezone.now()
    if action == "verify":
        StudentReportedScore.objects.filter(
            tenant=tenant,
            student=row.student,
            source=row.source,
            academic_year=row.academic_year,
            semester=row.semester,
            exam_round=row.exam_round,
            exam_month=row.exam_month,
            subject=row.subject,
            status=StudentReportedScore.Status.VERIFIED,
        ).exclude(id=row.id).update(
            status=StudentReportedScore.Status.REJECTED,
            reviewed_by=reviewed_by,
            reviewed_at=now,
            review_note="새로 확인된 성적표로 대체됨",
        )
        row.status = StudentReportedScore.Status.VERIFIED
    else:
        row.status = StudentReportedScore.Status.REJECTED
    row.reviewed_by = reviewed_by
    row.reviewed_at = now
    row.review_note = (review_note or "").strip()[:300]
    row.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note", "updated_at"])
    return row
