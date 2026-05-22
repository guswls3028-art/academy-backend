# PATH: apps/domains/students/services/lecture_enroll.py
# 강의 엑셀 원테이크 등록 시 "이름+학부모전화"로 기존 학생 찾거나 신규 생성

import re
from django.db import transaction

from academy.adapters.db.django import repositories_students as student_repo
from apps.core.models import TenantMembership
from apps.domains.parents.services import ensure_parent_for_student
from .lifecycle import restore_student
from .school import normalize_school_from_name

from ..ps_number import _generate_unique_ps_number


def _grade_value(v, school_type="HIGH"):
    if v is None:
        return None
    try:
        n = int(v)
        from .school import is_valid_grade
        return n if is_valid_grade(school_type, n) else None
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
        restore_data = dict(item)
        restore_data["name"] = name
        restore_data["parent_phone"] = parent_phone
        if phone is not None:
            restore_data["phone"] = phone
        restored_result = restore_student(
            deleted_student,
            tenant=tenant,
            profile_data=restore_data,
        )
        return restored_result.student, False, True

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
        st, elementary_school, high_school, middle_school = normalize_school_from_name(
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
            elementary_school=elementary_school,
            high_school=high_school,
            middle_school=middle_school,
            high_school_class=high_school_class,
            major=major,
            grade=_grade_value(item.get("grade"), st),
            memo=(item.get("memo") or "").strip() or None,
            is_managed=item.get("is_managed", True),
        )

        TenantMembership.ensure_active(
            tenant=tenant,
            user=user,
            role="student",
        )
        return student, True, False
