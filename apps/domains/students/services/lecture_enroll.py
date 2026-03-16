# PATH: apps/domains/students/services/lecture_enroll.py
# 강의 엑셀 원테이크 등록 시 "이름+학부모전화"로 기존 학생 찾거나 신규 생성

import re
from django.db import transaction

from academy.adapters.db.django import repositories_students as student_repo
from apps.core.models import TenantMembership
from apps.domains.parents.services import ensure_parent_for_student
from .school import normalize_school_from_name

from ..ps_number import _generate_unique_ps_number


def _grade_value(v):
    if v is None:
        return None
    try:
        n = int(v)
        return n if 1 <= n <= 3 else None
    except (TypeError, ValueError):
        return None


def _normalize_phone(value):
    if value is None:
        return ""
    s = re.sub(r"\D", "", str(value))
    return s.strip()


def get_or_create_student_for_lecture_enroll(tenant, item, password):
    """
    엑셀 한 행으로 기존 학생 조회 또는 신규 생성.
    item: name, parent_phone(필수), phone(선택), school, school_type, grade, memo, uses_identifier 등
    Returns: (Student, created: bool, was_restored: bool)
    """
    name = (item.get("name") or "").strip()
    parent_phone = _normalize_phone(item.get("parent_phone") or "")
    phone_raw = item.get("phone")
    phone = _normalize_phone(phone_raw) if phone_raw else None
    if phone and (len(phone) != 11 or not phone.startswith("010")):
        phone = None
    if not parent_phone or len(parent_phone) != 11 or not parent_phone.startswith("010"):
        return None, False, False  # 학부모 전화 필수; 실패 시 스킵

    # 1) 기존 활성 학생 조회: 이름 + 학부모전화 일치 (tenant, 삭제 안 된 것만)
    existing = student_repo.student_filter_tenant_name_parent_phone_active(
        tenant, name, parent_phone
    )
    if existing:
        return existing, False, False

    # 2) 소프트 삭제된 학생 조회: 동일 이름+학부모전화면 복원 후 재사용 (중복 생성 방지)
    deleted_student = student_repo.student_filter_tenant_name_parent_phone_deleted(
        tenant, name, parent_phone
    )
    if deleted_student:
        update_fields = ["deleted_at"]
        deleted_student.deleted_at = None
        if phone is not None:
            deleted_student.phone = phone
            update_fields.append("phone")
        school_val = (item.get("school") or "").strip() or None
        if school_val is not None:
            st, high_school, middle_school = normalize_school_from_name(
                school_val, item.get("school_type")
            )
            deleted_student.school_type = st
            deleted_student.high_school = high_school
            deleted_student.middle_school = middle_school
            deleted_student.high_school_class = (
                (item.get("high_school_class") or "").strip() or None
                if st == "HIGH"
                else None
            )
            deleted_student.major = (
                (item.get("major") or "").strip() or None if st == "HIGH" else None
            )
            update_fields.extend(
                ["school_type", "high_school", "middle_school", "high_school_class", "major"]
            )
        gr = _grade_value(item.get("grade"))
        if gr is not None:
            deleted_student.grade = gr
            update_fields.append("grade")
        if item.get("memo") is not None:
            deleted_student.memo = (item.get("memo") or "").strip() or None
            update_fields.append("memo")
        if item.get("gender") is not None:
            deleted_student.gender = (item.get("gender") or "").strip().upper()[:1] or None
            update_fields.append("gender")
        deleted_student.save(update_fields=update_fields)
        # Reactivate user account + phone 복원
        if deleted_student.user:
            user_update = ["is_active"]
            deleted_student.user.is_active = True
            if not deleted_student.user.phone and (phone or deleted_student.phone):
                deleted_student.user.phone = phone or deleted_student.phone
                user_update.append("phone")
            deleted_student.user.save(update_fields=user_update)
        # Unmangle ps_number (충돌 검사 포함)
        if deleted_student.ps_number and deleted_student.ps_number.startswith("_del_"):
            parts = deleted_student.ps_number.split("_", 3)  # ["", "del", "{id}", "{original}"]
            if len(parts) >= 4:
                original_ps = parts[3]
                from apps.domains.students.models import Student as StudentModel
                if not StudentModel.objects.filter(
                    tenant=tenant, ps_number=original_ps, deleted_at__isnull=True
                ).exists():
                    deleted_student.ps_number = original_ps
                    deleted_student.save(update_fields=["ps_number"])
                # 충돌 시 _del_ 접두사 유지 (새 ps_number 자동 생성하지 않음 — 관리자 수동 처리 필요)
        # 학부모 재연결 (삭제 시 parent_id가 None으로 해제됨)
        if not deleted_student.parent_id and parent_phone:
            try:
                parent = ensure_parent_for_student(
                    tenant=tenant,
                    parent_phone=parent_phone,
                    student_name=name,
                )
                if parent:
                    deleted_student.parent = parent
                    deleted_student.save(update_fields=["parent"])
            except Exception:
                pass  # 학부모 연결 실패 시 무시 (학생 복원은 계속)
        # NOTE: 이전 수강등록은 재활성화하지 않음.
        # 복원된 학생은 새로운 수강등록만 받아야 함 (이전 수강 이력이 유령처럼 되살아나는 것 방지).
        TenantMembership.ensure_active(
            tenant=tenant,
            user=deleted_student.user,
            role="student",
        )
        return deleted_student, False, True  # was_restored=True

    # 3) 신규 생성 (bulk_create 한 건 분 로직)
    # 학생 아이디: 본인 전화번호 우선, 없으면 랜덤
    if phone and not student_repo.student_filter_tenant_ps_number(tenant, phone).exists():
        ps_number = phone
    else:
        ps_number = _generate_unique_ps_number(tenant=tenant)
    omr_code = (phone[-8:] if phone and len(phone) >= 8 else parent_phone[-8:]).ljust(8, "0")[:8]

    with transaction.atomic():
        if phone:
            if student_repo.user_filter_phone_active(phone, tenant=tenant).exists():
                import logging as _log
                _log.getLogger(__name__).info(
                    "[lecture_enroll] skip name=%r: phone=%s already active in tenant",
                    name, phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else "***",
                )
                return None, False, False
        if student_repo.student_filter_tenant_ps_number(tenant, ps_number).exists():
            import logging as _log
            _log.getLogger(__name__).warning(
                "[lecture_enroll] skip name=%r: ps_number=%s collision (should not happen with tenant check)",
                name, ps_number,
            )
            return None, False, False

        parent = None
        if parent_phone:
            parent = ensure_parent_for_student(
                tenant=tenant,
                parent_phone=parent_phone,
                student_name=name,
            )

        user = student_repo.user_create_user(
            username=ps_number,
            tenant=tenant,
            phone=phone or "",
            name=name,
        )
        user.set_password(password)
        user.save()

        school_val = (item.get("school") or "").strip() or None
        st, high_school, middle_school = normalize_school_from_name(
            school_val, item.get("school_type")
        )
        high_school_class = (item.get("high_school_class") or "").strip() or None if st == "HIGH" else None
        major = (item.get("major") or "").strip() or None if st == "HIGH" else None

        student = student_repo.student_create(
            tenant=tenant,
            user=user,
            parent=parent,
            name=name,
            phone=phone,
            parent_phone=parent_phone,
            ps_number=ps_number,
            omr_code=omr_code,
            uses_identifier=item.get("uses_identifier", False) or (phone is None or not phone),
            gender=(item.get("gender") or "").strip().upper()[:1] or None,
            school_type=st,
            high_school=high_school,
            middle_school=middle_school,
            high_school_class=high_school_class,
            major=major,
            grade=_grade_value(item.get("grade")),
            memo=(item.get("memo") or "").strip() or None,
            is_managed=item.get("is_managed", True),
        )

        TenantMembership.ensure_active(
            tenant=tenant,
            user=user,
            role="student",
        )
        return student, True, False
