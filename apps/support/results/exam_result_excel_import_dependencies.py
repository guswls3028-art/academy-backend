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


def assigned_exam_enrollments(*, tenant, exam_ids):
    """Prefer explicit assignment per exam, otherwise use its same-course lesson enrollments."""
    from apps.domains.enrollment.models import SessionEnrollment
    from apps.domains.exams.models import Exam, ExamEnrollment
    from django.db.models import F

    links = set(Exam.objects.filter(tenant=tenant, pk__in=exam_ids, sessions__lecture__tenant=tenant)
                .values_list("pk", "sessions__lecture_id"))
    pairs = set()
    explicit = set()
    for exam, course, enrollment in ExamEnrollment.objects.filter(exam_id__in=exam_ids, enrollment__tenant=tenant).values_list(
            "exam_id", "enrollment__lecture_id", "enrollment_id"):
        if (exam, course) in links:
            explicit.add(exam)
            pairs.add((exam, enrollment))
    remaining = set(exam_ids) - explicit
    if remaining:
        pairs.update(SessionEnrollment.objects.filter(
            tenant=tenant, session__exams__id__in=remaining, session__exams__tenant=tenant,
            session__lecture__tenant=tenant, enrollment__tenant=tenant,
            enrollment__lecture_id=F("session__lecture_id"), enrollment__status="ACTIVE",
            enrollment__student__deleted_at__isnull=True,
        ).values_list("session__exams__id", "enrollment_id").distinct())
    return pairs


def get_result_import_candidates(
    *,
    exam_id: int,
    tenant: Any,
) -> list[ResultImportCandidateRecord]:
    from apps.domains.enrollment.models import Enrollment
    enrollment_ids = [eid for _, eid in assigned_exam_enrollments(tenant=tenant, exam_ids=[exam_id])]

    enrollments = (
        Enrollment.objects.filter(
            id__in=enrollment_ids,
            tenant=tenant, lecture__tenant=tenant, student__tenant=tenant,
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


def get_locked_exam_for_tenant(*, exam_id: int, tenant: Any) -> Any | None:
    from apps.domains.exams.models import Exam

    return Exam.objects.select_for_update().filter(id=int(exam_id), tenant=tenant).first()


def has_active_exam_submission(*, exam_id: int, enrollment_id: int, tenant: Any) -> bool:
    from apps.domains.submissions.models import Submission

    return Submission.objects.filter(
        tenant=tenant, target_type=Submission.TargetType.EXAM,
        target_id=int(exam_id), enrollment_id=int(enrollment_id),
    ).exclude(status__in=[Submission.Status.FAILED, Submission.Status.SUPERSEDED]).exists()
