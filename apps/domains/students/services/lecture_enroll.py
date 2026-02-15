# PATH: apps/domains/students/services/lecture_enroll.py
# 강의 엑셀 원테이크 등록 시 "이름+학부모전화"로 기존 학생 찾거나 신규 생성

import re
from django.db import transaction

from academy.adapters.db.django import repositories_students as student_repo
from apps.core.models import TenantMembership
from apps.domains.parents.services import ensure_parent_for_student
from .school import normalize_school_from_name

from ..serializers import _generate_unique_ps_number


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
    Returns: (Student, created: bool)
    """
    name = (item.get("name") or "").strip()
    parent_phone = _normalize_phone(item.get("parent_phone") or "")
    phone_raw = item.get("phone")
    phone = _normalize_phone(phone_raw) if phone_raw else None
    if phone and (len(phone) != 11 or not phone.startswith("010")):
        phone = None
    if not parent_phone or len(parent_phone) != 11 or not parent_phone.startswith("010"):
        return None, False  # 학부모 전화 필수; 실패 시 스킵

    # 1) 기존 활성 학생 조회: 이름 + 학부모전화 일치 (tenant, 삭제 안 된 것만)
    existing = student_repo.student_filter_tenant_name_parent_phone_active(
        tenant, name, parent_phone
    )
    if existing:
        return existing, False

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
        TenantMembership.ensure_active(
            tenant=tenant,
            user=deleted_student.user,
            role="student",
        )
        return deleted_student, False

    # 3) 신규 생성 (bulk_create 한 건 분 로직)
    ps_number = _generate_unique_ps_number()
    omr_code = (phone[-8:] if phone and len(phone) >= 8 else parent_phone[-8:]).ljust(8, "0")[:8]

    with transaction.atomic():
        if phone:
            if student_repo.user_filter_phone_active(phone).exists():
                return None, False
        if student_repo.student_filter_tenant_ps_number(tenant, ps_number).exists():
            return None, False

        parent = None
        if parent_phone:
            parent = ensure_parent_for_student(
                tenant=tenant,
                parent_phone=parent_phone,
                student_name=name,
                parent_password=password,
            )

        user = student_repo.user_create_user(
            username=ps_number,
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
        return student, True
