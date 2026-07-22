"""Cross-domain reads used by the exam result Excel import workflow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ResultImportQuestionRecord:
    question_id: int
    number: int
    score: float


@dataclass(frozen=True)
class ResultImportCandidateRecord:
    enrollment_id: int
    student_name: str
    student_phone: str
    parent_phone: str
    school: str
    lecture_id: int | None
    lecture_title: str
    lecture_color: str
    lecture_chip_label: str


def get_result_import_questions(*, sheet: Any) -> list[ResultImportQuestionRecord]:
    from apps.domains.exams.models import ExamQuestion

    return [
        ResultImportQuestionRecord(
            question_id=int(question.id),
            number=int(question.number),
            score=float(question.score or 0.0),
        )
        for question in ExamQuestion.objects.filter(sheet=sheet)
        .only("id", "number", "score")
        .order_by("number")
    ]


def get_answer_key_answers(*, template_exam_id: int | None) -> Any:
    if not template_exam_id:
        return None

    from apps.domains.exams.models import AnswerKey

    answer_key = (
        AnswerKey.objects.filter(exam_id=int(template_exam_id)).only("answers").first()
    )
    return getattr(answer_key, "answers", None)


def get_result_import_candidates(
    *,
    exam_id: int,
    tenant: Any,
) -> list[ResultImportCandidateRecord]:
    from apps.domains.enrollment.models import Enrollment, SessionEnrollment
    from apps.domains.exams.models import ExamEnrollment

    enrollment_ids = list(
        ExamEnrollment.objects.filter(
            exam_id=int(exam_id),
            enrollment__tenant=tenant,
        ).values_list("enrollment_id", flat=True)
    )
    if not enrollment_ids:
        enrollment_ids = list(
            SessionEnrollment.objects.filter(
                tenant=tenant,
                session__exams__id=int(exam_id),
                session__exams__tenant=tenant,
                session__lecture__tenant=tenant,
                enrollment__tenant=tenant,
                enrollment__status="ACTIVE",
                enrollment__student__deleted_at__isnull=True,
            )
            .values_list("enrollment_id", flat=True)
            .distinct()
        )

    enrollments = (
        Enrollment.objects.filter(
            id__in=enrollment_ids,
            tenant=tenant,
            status="ACTIVE",
            student__deleted_at__isnull=True,
        )
        .select_related("student", "lecture")
        .order_by("student__name", "id")
    )
    records: list[ResultImportCandidateRecord] = []
    for enrollment in enrollments:
        student = enrollment.student
        lecture = enrollment.lecture
        school = ""
        if student.school_type == "ELEMENTARY":
            school = str(student.elementary_school or "")
        elif student.school_type == "MIDDLE":
            school = str(student.middle_school or "")
        elif student.school_type == "HIGH":
            school = str(student.high_school or "")
        records.append(
            ResultImportCandidateRecord(
                enrollment_id=int(enrollment.id),
                student_name=str(student.name or ""),
                student_phone=str(student.phone or ""),
                parent_phone=str(student.parent_phone or ""),
                school=school,
                lecture_id=int(lecture.id) if lecture else None,
                lecture_title=str(getattr(lecture, "title", "") or ""),
                lecture_color=str(getattr(lecture, "color", "") or ""),
                lecture_chip_label=str(getattr(lecture, "chip_label", "") or ""),
            )
        )
    return records


def get_locked_enrollment_for_tenant(*, enrollment_id: int, tenant: Any) -> Any | None:
    from apps.domains.enrollment.models import Enrollment

    return (
        Enrollment.objects.select_for_update()
        .filter(id=int(enrollment_id), tenant=tenant)
        .first()
    )
