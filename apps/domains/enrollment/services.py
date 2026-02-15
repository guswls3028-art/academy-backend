# PATH: apps/domains/enrollment/services.py
# 강의 엑셀 원테이크 로직 — API 뷰와 워커에서 공통 사용 (헥사고날: 애플리케이션 서비스 계층)

from __future__ import annotations

import logging

from django.db import transaction

from academy.adapters.db.django import repositories_enrollment as enroll_repo
from apps.domains.lectures.models import Session, Lecture
from apps.domains.students.services import get_or_create_student_for_lecture_enroll

from .models import Enrollment, SessionEnrollment

logger = logging.getLogger(__name__)


def _mask_phone(phone: str) -> str:
    """로깅용 전화번호 마스킹 (앞3·뒤4만 노출)"""
    if not phone or len(phone) < 7:
        return "***"
    return f"{phone[:3]}****{phone[-4:]}"


def lecture_enroll_from_excel_rows(
    *,
    tenant_id: int,
    lecture_id: int,
    students_data: list[dict],
    initial_password: str,
    session_id: int | None = None,
) -> dict:
    """
    엑셀 파싱된 행으로 강의 수강 등록 + 차시 등록·출결.
    session_id가 있으면 해당 차시에만 등록, 없으면 1차시 생성/사용 후 등록.
    API(lecture_enroll_from_excel)와 워커(EXCEL_PARSING)에서 공통 호출.
    """
    tenant = enroll_repo.get_tenant_by_id(tenant_id)
    if not tenant:
        raise ValueError("tenant_id not found")

    lecture = enroll_repo.get_lecture_by_id_tenant_id(lecture_id, tenant)
    if not lecture:
        raise ValueError("해당 학원의 강의가 아닙니다.")

    initial_password = (initial_password or "").strip()
    if len(initial_password) < 4:
        raise ValueError("initial_password는 4자 이상이어야 합니다.")

    with transaction.atomic():
        student_ids: list[int] = []
        created_student_count = 0
        seen: set[tuple[str, str]] = set()

        for row_index, item in enumerate(students_data, start=1):
            raw = dict(item) if isinstance(item, dict) else {}
            name = (raw.get("name") or "").strip()
            parent_phone = (raw.get("parent_phone") or raw.get("parentPhone") or "")
            parent_phone = "".join(c for c in parent_phone if c.isdigit())

            if not name or len(parent_phone) != 11 or not parent_phone.startswith("010"):
                logger.debug(
                    "[lecture_enroll_excel] row=%s skip name=%r parent_phone_len=%s reason=invalid_or_missing",
                    row_index,
                    name or "(empty)",
                    len(parent_phone),
                )
                continue
            key = (name, parent_phone)
            if key in seen:
                logger.debug(
                    "[lecture_enroll_excel] row=%s skip name=%r parent=%s reason=duplicate",
                    row_index,
                    name,
                    _mask_phone(parent_phone),
                )
                continue
            seen.add(key)

            row = {
                "name": name,
                "parent_phone": parent_phone,
                "phone": raw.get("phone") or raw.get("studentPhone"),
                "school": raw.get("school"),
                "school_type": raw.get("school_type"),
                "grade": raw.get("grade"),
                "memo": raw.get("memo"),
                "uses_identifier": raw.get("uses_identifier", False),
                "gender": raw.get("gender"),
                "high_school_class": raw.get("high_school_class"),
                "major": raw.get("major"),
            }
            try:
                student, created = get_or_create_student_for_lecture_enroll(tenant, row, initial_password)
            except Exception as e:
                logger.warning(
                    "[lecture_enroll_excel] row=%s name=%r parent=%s error=%s",
                    row_index,
                    name,
                    _mask_phone(parent_phone),
                    e,
                    exc_info=True,
                )
                raise
            if student:
                student_ids.append(student.id)
                if created:
                    created_student_count += 1
                logger.debug(
                    "[lecture_enroll_excel] row=%s name=%r student_id=%s created=%s",
                    row_index,
                    name,
                    student.id,
                    created,
                )
            else:
                logger.debug(
                    "[lecture_enroll_excel] row=%s name=%r skip reason=student_resolve_failed",
                    row_index,
                    name,
                )

        if not student_ids:
            raise ValueError(
                "등록할 수 있는 학생이 없습니다. 이름·학부모 전화번호(010 11자리)를 확인해 주세요."
            )

        enrollments_created: list = []
        for sid in student_ids:
            if not enroll_repo.student_exists(sid, tenant):
                logger.warning("[lecture_enroll_excel] student_id=%s not in tenant, skip", sid)
                continue
            obj, created = enroll_repo.enrollment_get_or_create_ret(
                tenant=tenant,
                lecture=lecture,
                student_id=sid,
                defaults={"status": "ACTIVE"},
            )
            enrollments_created.append(obj)
            if created:
                logger.debug("[lecture_enroll_excel] enrollment created lecture_id=%s student_id=%s", lecture_id, sid)

        if session_id:
            target_session = enroll_repo.get_session_by_id_lecture(session_id, lecture)
            if not target_session:
                raise ValueError("해당 차시가 이 강의의 차시가 아닙니다.")
        else:
            target_session = enroll_repo.get_session_by_lecture_order(lecture, 1)
            if not target_session:
                target_session = enroll_repo.create_session_with_meta(
                    lecture=lecture,
                    order=1,
                    title="1차시",
                    date=lecture.start_date,
                )
                logger.info(
                    "[lecture_enroll_excel] session created lecture_id=%s session_id=%s order=1",
                    lecture_id,
                    target_session.id,
                )

        for enrollment in enrollments_created:
            enroll_repo.session_enrollment_get_or_create_tenant(
                tenant=tenant,
                session=target_session,
                enrollment=enrollment,
            )
            enroll_repo.attendance_get_or_create_tenant(
                tenant=tenant,
                enrollment=enrollment,
                session=target_session,
                defaults={"status": "PRESENT"},
            )

        return {
            "enrolled_count": len(enrollments_created),
            "created_students_count": created_student_count,
            "session_id": target_session.id,
        }
