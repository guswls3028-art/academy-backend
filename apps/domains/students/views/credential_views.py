# PATH: apps/domains/students/views/credential_views.py

from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from apps.core.permissions import TenantResolved
from apps.api.common.throttles import SmsEndpointThrottle

from .password_views import _normalize_phone_for_reset


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
        # 이름 필수: phone-only 일치로 임의 학생 비밀번호를 강제 재설정(=lock-out DoS) 방어.
        if not name:
            return Response({"detail": "학생 이름을 입력해 주세요."}, status=400)

        from apps.domains.students.services.account_recovery import (
            AccountRecoveryDeliveryError,
            AccountRecoveryValidationError,
            resolve_recovery_account,
            send_password_recovery,
            validate_recovery_payload,
        )

        try:
            _, target, student_name, verified_phone = validate_recovery_payload(
                mode="password",
                target="student",
                name=name,
                phone=phone,
            )
        except AccountRecoveryValidationError as e:
            return Response({"detail": e.detail}, status=400)

        message = "입력한 정보가 등록되어 있다면 해당 번호로 아이디와 임시 비밀번호가 발송됩니다."
        account = resolve_recovery_account(
            tenant=tenant,
            target=target,
            name=student_name,
            phone=verified_phone,
        )
        if account is None:
            return Response({"message": message}, status=200)

        try:
            send_password_recovery(account)
        except AccountRecoveryValidationError as e:
            return Response({"detail": e.detail}, status=400)
        except AccountRecoveryDeliveryError as e:
            return Response({"detail": e.detail}, status=503)

        return Response({"message": message}, status=200)
