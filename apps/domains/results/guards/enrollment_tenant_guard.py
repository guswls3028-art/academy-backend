# PATH: apps/domains/results/guards/enrollment_tenant_guard.py
"""
enrollment_id가 해당 tenant에 속하는지 검증하는 가드.
admin 뷰에서 URL path로 받은 enrollment_id의 테넌트 격리를 보장한다.
"""
from __future__ import annotations

from rest_framework.exceptions import NotFound

from apps.domains.enrollment.models import Enrollment


def validate_enrollment_belongs_to_tenant(enrollment_id: int, tenant) -> Enrollment:
    """
    enrollment_id가 tenant에 속하는지 검증.
    속하지 않으면 NotFound (정보 유출 방지를 위해 404 사용).
    """
    try:
        return Enrollment.objects.get(id=enrollment_id, tenant=tenant)
    except Enrollment.DoesNotExist:
        raise NotFound("해당 수강 등록 정보를 찾을 수 없습니다.")
