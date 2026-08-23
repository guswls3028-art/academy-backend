# PATH: apps/domains/enrollment/services.py
# 강의 엑셀 원테이크 로직 — API 뷰와 워커에서 공통 사용 (헥사고날: 애플리케이션 서비스 계층)

from __future__ import annotations

import logging

from django.db import transaction

from academy.adapters.db.django import repositories_enrollment as enroll_repo
from apps.support.enrollment.import_dependencies import (
    StudentImportIdentityAmbiguousError,
    active_student_for_import_identity,
)
from apps.support.enrollment.lifecycle_dependencies import (
    schedule_pending_account_notice,
)

from ..models import Enrollment, SessionEnrollment

logger = logging.getLogger(__name__)


def _mask_phone(phone: str) -> str:
    """로깅용 전화번호 마스킹 (앞3·뒤4만 노출)"""
    if not phone or len(phone) < 7:
        return "***"
    return f"{phone[:3]}****{phone[-4:]}"


def _existing_student_ps_number(raw: dict) -> str:
    direct = raw.get("ps_number") or raw.get("psNumber")
    if direct:
        return str(direct).strip()
    extra_columns = raw.get("_extra_columns")
    if not isinstance(extra_columns, dict):
        return ""
    identifier_headers = {
        "학생번호",
        "학생아이디",
        "ps번호",
        "ps_number",
        "studentid",
        "studentidentifier",
    }
    for header, value in extra_columns.items():
        normalized_header = "".join(str(header or "").split()).lower()
        if normalized_header in identifier_headers and value:
            return str(value).strip()
    return ""


def lecture_enroll_from_excel_rows(
    *,
    tenant_id: int,
    lecture_id: int,
    students_data: list[dict],
    initial_password: str = "",
    password_mode: str = "existing_only",
    session_id: int | None = None,
    source_job_id: str = "",
) -> dict:
    """
    엑셀 파싱된 행을 기존 학생 명부와 매칭해 강의 수강 등록 + 차시 등록·출결.
    학생번호가 있으면 exact 우선 매칭하고, 없으면 exact 이름과 정규화한
    학부모 전화번호 조합으로만 매칭한다.
    session_id가 있으면 해당 차시에만 등록, 없으면 1차시 생성/사용 후 등록.
    API(lecture_enroll_from_excel)와 워커(EXCEL_PARSING)에서 공통 호출.

    initial_password/password_mode/source_job_id는 배포 전 큐 payload 호환을 위해
    받지만 수강 등록에서는 사용하지 않는다. 신규 학생 생성은 학생 등록 흐름만
    소유한다.
    """
    tenant = enroll_repo.get_tenant_by_id(tenant_id)
    if not tenant:
        raise ValueError("tenant_id not found")

    lecture = enroll_repo.get_lecture_by_id_tenant_id(lecture_id, tenant)
    if not lecture:
        raise ValueError("해당 학원의 강의가 아닙니다.")

    with transaction.atomic():
        student_ids: list[int] = []
        seen: set[tuple[str, ...]] = set()
        seen_student_ids: set[int] = set()
        not_found_students_count = 0
        ambiguous_students_count = 0

        skipped_reasons: list[str] = []
        for row_index, item in enumerate(students_data, start=1):
            raw = dict(item) if isinstance(item, dict) else {}
            ps_number = _existing_student_ps_number(raw)
            name = (raw.get("name") or "").strip()
            parent_phone = (raw.get("parent_phone") or raw.get("parentPhone") or "")
            parent_phone = "".join(c for c in str(parent_phone) if c.isdigit())

            if not ps_number and (
                not name
                or len(parent_phone) != 11
                or not parent_phone.startswith("010")
            ):
                reason = []
                if not name:
                    reason.append("name_empty")
                if len(parent_phone) != 11:
                    reason.append(f"phone_len={len(parent_phone)}")
                if parent_phone and not parent_phone.startswith("010"):
                    reason.append("phone_not_010")
                reason_str = ",".join(reason) or "unknown"
                skipped_reasons.append(f"row{row_index}:{reason_str}")
                logger.debug(
                    "[lecture_enroll_excel] row=%s skip name=%r parent_phone_len=%s reason=%s",
                    row_index,
                    name or "(empty)",
                    len(parent_phone),
                    reason_str,
                )
                continue
            key = (
                ("ps_number", ps_number)
                if ps_number
                else ("name_parent", name, parent_phone)
            )
            if key in seen:
                logger.debug(
                    "[lecture_enroll_excel] row=%s skip name=%r parent=%s reason=duplicate",
                    row_index,
                    name,
                    _mask_phone(parent_phone),
                )
                continue
            seen.add(key)

            try:
                student = active_student_for_import_identity(
                    tenant,
                    ps_number=ps_number,
                    name=name,
                    parent_phone=parent_phone,
                    for_update=True,
                )
            except StudentImportIdentityAmbiguousError:
                ambiguous_students_count += 1
                skipped_reasons.append(f"row{row_index}:student_identity_ambiguous")
                logger.warning(
                    "[lecture_enroll_excel] row=%s ps_number=%r name=%r parent=%s skip=%s",
                    row_index,
                    ps_number or "(empty)",
                    name,
                    _mask_phone(parent_phone),
                    "student_identity_ambiguous",
                )
                continue
            if student is None:
                not_found_students_count += 1
                skipped_reasons.append(f"row{row_index}:student_not_found")
                logger.debug(
                    "[lecture_enroll_excel] row=%s ps_number=%r name=%r parent=%s skip=%s",
                    row_index,
                    ps_number or "(empty)",
                    name,
                    _mask_phone(parent_phone),
                    "student_not_found",
                )
                continue
            if student.id in seen_student_ids:
                logger.debug(
                    "[lecture_enroll_excel] row=%s student_id=%s skip=resolved_student_duplicate",
                    row_index,
                    student.id,
                )
                continue
            seen_student_ids.add(student.id)
            student_ids.append(student.id)
            logger.debug(
                "[lecture_enroll_excel] row=%s ps_number=%r name=%r student_id=%s matched_existing=true",
                row_index,
                ps_number or "(empty)",
                name,
                student.id,
            )

        if not student_ids:
            total_rows = len(students_data)
            skipped_count = len(skipped_reasons)
            logger.error(
                "[lecture_enroll_excel] ALL students skipped: total=%s skipped=%s reasons=%s",
                total_rows,
                skipped_count,
                "; ".join(skipped_reasons[:10]),
            )
            if ambiguous_students_count:
                raise ValueError(
                    "학생 명부에 동일한 이름·학부모 전화번호가 중복되어 등록 대상을 "
                    "확정할 수 없습니다. 학생 명부를 정리한 뒤 다시 시도해 주세요."
                )
            if not_found_students_count:
                raise ValueError(
                    "학생 명부에서 등록할 수 있는 학생을 찾지 못했습니다. "
                    "먼저 학생 등록을 완료한 뒤 이름·학부모 전화번호가 같은지 확인해 주세요."
                )
            raise ValueError(
                f"등록할 수 있는 학생이 없습니다. "
                f"전체 {total_rows}행 중 {skipped_count}행 건너뜀. "
                f"이름·학부모 전화번호(010 11자리)를 확인해 주세요."
            )

        enrollments_created: list = []
        for sid in student_ids:
            if not enroll_repo.active_student_exists(sid, tenant):
                logger.warning("[lecture_enroll_excel] student_id=%s not active in tenant, skip", sid)
                continue
            obj, created = enroll_repo.enrollment_get_or_create_ret(
                tenant=tenant,
                lecture=lecture,
                student_id=sid,
                defaults={"status": "ACTIVE"},
            )
            if not created and obj.status != "ACTIVE":
                obj.status = "ACTIVE"
                obj.save(update_fields=["status"])
            enrollments_created.append(obj)
            schedule_pending_account_notice(student_id=obj.student_id)
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
                defaults={"status": "UNSET"},
            )

        result = {
            "enrolled_count": len(enrollments_created),
            "created_students_count": 0,
            "not_found_students_count": not_found_students_count,
            "ambiguous_students_count": ambiguous_students_count,
            "session_id": target_session.id,
        }
        return result
