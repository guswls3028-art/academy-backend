# PATH: apps/domains/students/views/credential_views.py

from django.db.models import Q

from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from apps.core.permissions import TenantResolved
from apps.api.common.throttles import SmsEndpointThrottle
from apps.core.models.user import user_display_username

from ..models import Student
from .password_views import _normalize_phone_for_reset, _generate_temp_password


class SendExistingCredentialsView(APIView):
    """
    POST: 이미 등록된 학생에게 기존 아이디 + 임시 비밀번호를 알림톡으로 발송.
    (회원가입 시 중복 감지 → "카카오톡으로 ID/비밀번호 발송" 버튼용)
    """
    permission_classes = [AllowAny, TenantResolved]
    throttle_classes = [SmsEndpointThrottle]

    def get_authenticators(self):
        return []

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant를 확인할 수 없습니다."}, status=400)

        phone = _normalize_phone_for_reset(request.data.get("phone") or "")
        name = (request.data.get("name") or "").strip()

        if not phone or len(phone) != 11:
            return Response({"detail": "전화번호를 입력해 주세요."}, status=400)

        # 학생 조회 (전화번호 또는 학부모전화번호 + 이름)
        qs = Student.objects.filter(tenant=tenant, deleted_at__isnull=True)
        if name:
            qs = qs.filter(name__iexact=name)
        student = qs.filter(
            Q(phone=phone) | Q(parent_phone=phone)
        ).select_related("user").first()

        if not student or not getattr(student, "user", None):
            return Response({"detail": "등록된 학생을 찾을 수 없습니다."}, status=404)

        # 임시 비밀번호 생성 및 저장
        temp_password = _generate_temp_password()
        old_password_hash = student.user.password  # 발송 실패 시 롤백용
        from apps.core.services.password import force_reset_password
        force_reset_password(student.user, temp_password)

        display_username = student.ps_number or user_display_username(student.user)
        send_to = (student.phone or "").strip()
        if not send_to or len(send_to) != 11:
            send_to = (student.parent_phone or "").strip()
        if not send_to or len(send_to) != 11:
            return Response({"detail": "등록된 전화번호가 없어 발송할 수 없습니다."}, status=400)

        from apps.support.messaging.policy import MessagingPolicyError, is_messaging_disabled

        if is_messaging_disabled(tenant.id):
            return Response({"message": "아이디/비밀번호가 발송되었습니다."}, status=200)

        # 오너 테넌트의 승인된 알림톡 템플릿으로 발송 (모든 테넌트 공통, SMS fallback 없음)
        from apps.support.messaging.policy import send_alimtalk_via_owner
        from django.conf import settings as _settings
        site_url = getattr(_settings, "SITE_URL", "") or "https://hakwonplus.com"
        ok = send_alimtalk_via_owner(
            trigger="password_reset_student",
            to=send_to,
            replacements={
                "학생이름": student.name or "",
                "학생아이디": display_username or "",
                "학생비밀번호": temp_password,
                "아이디": display_username or "",
                "임시비밀번호": temp_password,
                "비밀번호안내": "접속해서 ID\xb7비밀번호를 변경할 수 있습니다.",
                "사이트링크": site_url,
            },
        )

        if not ok:
            # 발송 실패 시 비밀번호 롤백
            from apps.core.services.password import rollback_password
            rollback_password(student.user, old_password_hash)
            return Response({"detail": "발송에 실패했습니다. 잠시 후 다시 시도해 주세요."}, status=503)
        return Response({"message": "아이디와 임시 비밀번호가 알림톡으로 발송되었습니다."}, status=200)
