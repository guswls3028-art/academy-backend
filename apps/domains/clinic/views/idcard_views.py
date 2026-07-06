# PATH: apps/domains/clinic/views/idcard_views.py
"""
학생 클리닉 인증(차시별 합불) 전용 API
GET /api/v1/clinic/idcard/
- 단일 진실: progress.ClinicLink(is_auto=True, resolved_at__isnull=True)
- 서버 기준 오늘 날짜 반환 (위조 방지)
"""
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolved
from apps.domains.clinic.color_utils import get_effective_clinic_colors
from apps.support.clinic.idcard_dependencies import (
    latest_active_enrollment_for_student,
    ordered_sessions_for_enrollment,
    student_for_idcard_user,
    unresolved_auto_clinic_session_ids,
)


def _profile_photo_url(request, student):
    if not getattr(student, "profile_photo", None):
        return None
    try:
        return request.build_absolute_uri(student.profile_photo.url)
    except (ValueError, AttributeError, Exception):
        return None


def _response_payload(
    *,
    student_name: str = "",
    profile_photo_url: str | None = None,
    colors: list[str],
    histories: list[dict] | None = None,
):
    now = timezone.now()
    histories = histories or []
    return {
        "student_name": student_name,
        "profile_photo_url": profile_photo_url,
        "background_colors": colors[:3],
        "server_date": now.date().isoformat(),
        "server_datetime": now.isoformat(),
        "histories": histories,
        "current_result": "FAIL" if any(h["clinic_required"] for h in histories) else "SUCCESS",
    }


class StudentClinicIdcardView(APIView):
    """
    GET /clinic/idcard/
    학생 본인 차시별 합불 + 클리닉 대상 여부.
    """
    permission_classes = [IsAuthenticated, TenantResolved]

    def get(self, request):
        user = request.user
        tenant = request.tenant
        student = student_for_idcard_user(tenant=tenant, user=user)

        # 패스카드 배경 색상 (매일 자동 3색 또는 저장값)
        colors = get_effective_clinic_colors(tenant) if tenant else ["#ef4444", "#3b82f6", "#22c55e"]

        if not student:
            return Response(_response_payload(colors=colors))

        # tenant is guaranteed by TenantResolved permission
        # enrollment 선택 SSOT: 가장 최근 활성 등록 (booking/ops console과 동일 규칙)
        enrollment = latest_active_enrollment_for_student(tenant=tenant, student=student)

        if not enrollment:
            return Response(
                _response_payload(
                    student_name=getattr(student, "name", "") or "",
                    profile_photo_url=_profile_photo_url(request, student),
                    colors=colors,
                )
            )

        # section_mode 대응: 학생이 배정된 반의 세션만 조회
        sessions = ordered_sessions_for_enrollment(enrollment)
        enrollment_id = enrollment.id
        clinic_links = unresolved_auto_clinic_session_ids(
            tenant=tenant,
            enrollment_id=enrollment_id,
        )

        histories = []
        for sess in sessions:
            clinic_required = sess.id in clinic_links
            histories.append({
                "session_order": sess.order,
                "passed": not clinic_required,
                "clinic_required": clinic_required,
            })

        # 프로필 사진 URL (신원 확인용) - 기존 방식 사용
        return Response(
            _response_payload(
                student_name=getattr(student, "name", "") or "",
                profile_photo_url=_profile_photo_url(request, student),
                colors=colors,
                histories=histories,
            )
        )
