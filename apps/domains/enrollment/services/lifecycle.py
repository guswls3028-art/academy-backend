"""Canonical enrollment write/use-case entrypoints."""

from __future__ import annotations

from django.db import transaction
from rest_framework.exceptions import NotFound, ValidationError

from academy.adapters.db.django import repositories_enrollment as enroll_repo
from apps.domains.enrollment.models import Enrollment, SessionEnrollment
from apps.domains.enrollment.selectors import (
    get_active_enrollment_for_student_lecture,
    get_lecture_for_tenant_or_404,
    get_session_for_lecture_or_404,
    get_student_for_tenant_or_404,
    require_tenant,
)
from apps.domains.fees.services import (
    auto_assign_fees_on_enrollment,
    deactivate_fees_for_enrollment,
)
from apps.domains.messaging.services import send_event_notification


def _validate_id_list(value, *, field_name: str, allow_empty: bool = False) -> list[int]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValidationError({"detail": f"{field_name}(list)는 필수입니다"})
    if len(value) > 200:
        raise ValidationError({"detail": "최대 200건까지 일괄 처리할 수 있습니다."})
    try:
        return [int(v) for v in value]
    except (TypeError, ValueError) as exc:
        raise ValidationError({"detail": f"{field_name} 값이 잘못되었습니다."}) from exc


def sync_enrollment_status_side_effects(enrollment: Enrollment) -> None:
    if enrollment.status != "ACTIVE":
        deactivate_fees_for_enrollment(enrollment)
        return
    auto_assign_fees_on_enrollment(
        enrollment.tenant,
        enrollment.student,
        enrollment.lecture,
        enrollment,
    )


def delete_enrollment(enrollment: Enrollment) -> None:
    enroll_repo.session_enrollment_filter_delete(enrollment.tenant, enrollment)
    deactivate_fees_for_enrollment(enrollment)
    enrollment.delete()


def bulk_create_enrollments(*, tenant, lecture_id, student_ids) -> list[Enrollment]:
    tenant = require_tenant(tenant)
    if not lecture_id:
        raise ValidationError({"detail": "lecture, students(list)는 필수입니다"})
    student_ids = _validate_id_list(student_ids, field_name="students")

    lecture = enroll_repo.get_lecture_by_id_tenant(lecture_id, tenant)
    if not lecture:
        raise ValidationError({"detail": "해당 학원의 강의가 아닙니다."})

    created: list[Enrollment] = []
    for sid in student_ids:
        if not enroll_repo.student_exists_for_tenant(sid, tenant):
            raise ValidationError({"detail": f"학생(id={sid})은 현재 학원 소속이 아닙니다."})

        obj, created_new = enroll_repo.enrollment_get_or_create(
            tenant=tenant,
            lecture=lecture,
            student_id=sid,
            defaults={"status": "ACTIVE"},
        )
        if not created_new and obj.status != "ACTIVE":
            obj.status = "ACTIVE"
            obj.save(update_fields=["status"])
        created.append(obj)

        student = getattr(obj, "student", None)
        if student:
            auto_assign_fees_on_enrollment(tenant, student, lecture, obj)

        if created_new and student:
            transaction.on_commit(
                lambda t=tenant, s=student, title=lecture.title: send_event_notification(
                    tenant=t,
                    trigger="class_enrollment_complete",
                    student=s,
                    send_to="parent",
                    context={"강의명": title},
                )
            )

    return created


def bulk_create_session_enrollments(*, tenant, session_id, enrollment_ids) -> list[SessionEnrollment]:
    tenant = require_tenant(tenant)
    if not session_id:
        raise ValidationError({"detail": "session, enrollments(list)는 필수입니다"})
    enrollment_ids = _validate_id_list(enrollment_ids, field_name="enrollments", allow_empty=True)

    session = enroll_repo.get_session_by_id_with_lecture(session_id)
    if session is None:
        raise ValidationError({"detail": "세션을 찾을 수 없습니다."})
    if session.lecture.tenant_id != tenant.id:
        raise ValidationError({"detail": "다른 학원의 세션입니다."})

    created: list[SessionEnrollment] = []
    for eid in enrollment_ids:
        enrollment = enroll_repo.get_enrollment_by_id_with_lecture(eid, tenant)
        if enrollment is None:
            raise ValidationError({"detail": f"수강 등록을 찾을 수 없습니다: {eid}"})
        if enrollment.lecture_id != session.lecture_id:
            raise ValidationError({"detail": "다른 강의 수강자는 이 세션에 추가할 수 없습니다."})

        if enrollment.status != "ACTIVE":
            enrollment.status = "ACTIVE"
            enrollment.save(update_fields=["status"])
        auto_assign_fees_on_enrollment(
            tenant,
            enrollment.student,
            session.lecture,
            enrollment,
        )

        obj, _ = enroll_repo.session_enrollment_get_or_create_tenant(
            tenant=tenant,
            session=session,
            enrollment=enrollment,
        )
        created.append(obj)
        enroll_repo.attendance_get_or_create_tenant(
            tenant=tenant,
            enrollment=enrollment,
            session=session,
            defaults={"status": "PRESENT"},
        )

    return created


def toggle_student_learning_access(
    *,
    tenant,
    student_id: int,
    lecture_id: int,
    target_type: str,
    target_id: int,
    action: str,
) -> dict:
    from apps.domains.exams.models import Exam, ExamEnrollment
    from apps.domains.homework.models import HomeworkAssignment
    from apps.domains.homework_results.models import Homework

    tenant = require_tenant(tenant)
    student = get_student_for_tenant_or_404(tenant=tenant, student_id=student_id)
    lecture = get_lecture_for_tenant_or_404(tenant=tenant, lecture_id=lecture_id)
    enrollment = get_active_enrollment_for_student_lecture(
        tenant=tenant,
        student=student,
        lecture=lecture,
    )
    if not enrollment:
        raise ValidationError({"detail": "강의 등록 없음"})

    if target_type == "session":
        session = get_session_for_lecture_or_404(lecture=lecture, session_id=target_id)
        if action == "add":
            SessionEnrollment.objects.get_or_create(
                tenant=tenant,
                enrollment=enrollment,
                session=session,
            )
        else:
            SessionEnrollment.objects.filter(
                tenant=tenant,
                enrollment=enrollment,
                session=session,
            ).delete()
    elif target_type == "exam":
        exam = (
            Exam.objects
            .filter(id=target_id, tenant=tenant, sessions__lecture=lecture)
            .distinct()
            .first()
        )
        if exam is None:
            raise NotFound("시험을 찾을 수 없습니다")

        if action == "add":
            first_session_id = (
                exam.sessions
                .filter(lecture=lecture)
                .order_by("order", "id")
                .values_list("id", flat=True)
                .first()
            )
            if first_session_id:
                SessionEnrollment.objects.get_or_create(
                    tenant=tenant,
                    enrollment=enrollment,
                    session_id=first_session_id,
                )
            ExamEnrollment.objects.get_or_create(exam=exam, enrollment=enrollment)
        else:
            ExamEnrollment.objects.filter(exam=exam, enrollment=enrollment).delete()
    elif target_type == "homework":
        homework = (
            Homework.objects
            .select_related("session", "session__lecture")
            .filter(id=target_id, tenant=tenant, session__lecture=lecture)
            .first()
        )
        if homework is None:
            raise NotFound("과제를 찾을 수 없습니다")

        if action == "add":
            SessionEnrollment.objects.get_or_create(
                tenant=tenant,
                enrollment=enrollment,
                session_id=homework.session_id,
            )
            HomeworkAssignment.objects.get_or_create(
                tenant=tenant,
                homework=homework,
                session_id=homework.session_id,
                enrollment=enrollment,
            )
        else:
            HomeworkAssignment.objects.filter(
                tenant=tenant,
                homework=homework,
                session_id=homework.session_id,
                enrollment=enrollment,
            ).delete()
    else:
        raise ValidationError({"detail": "target_type 잘못됨"})

    return {"ok": True, "target_type": target_type, "target_id": target_id, "action": action}
