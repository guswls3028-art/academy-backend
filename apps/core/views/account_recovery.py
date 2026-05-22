# PATH: apps/core/views/account_recovery.py

from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.common.throttles import SmsEndpointThrottle
from apps.core.permissions import TenantResolved
from apps.domains.students.services.account_recovery import (
    AccountRecoveryDeliveryError,
    AccountRecoveryValidationError,
    resolve_recovery_account,
    send_password_recovery,
    send_username_recovery,
    validate_recovery_payload,
)


GENERIC_MESSAGES = {
    "username": "입력한 정보가 등록되어 있다면 해당 번호로 아이디 안내가 발송됩니다.",
    "password": "입력한 정보가 등록되어 있다면 해당 번호로 임시 비밀번호가 발송됩니다.",
}


class AccountRecoveryDispatchView(APIView):
    """
    Canonical public account recovery endpoint.

    POST:
      {
        "mode": "username" | "password",
        "target": "student" | "parent",
        "student_name": "...",
        "phone": "01012345678"
      }
    """

    permission_classes = [AllowAny, TenantResolved]
    throttle_classes = [SmsEndpointThrottle]

    def get_authenticators(self):
        return []

    def post(self, request):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant를 확인할 수 없습니다."}, status=400)

        try:
            mode, target, name, phone = validate_recovery_payload(
                mode=request.data.get("mode"),
                target=request.data.get("target"),
                name=request.data.get("student_name") or request.data.get("name"),
                phone=request.data.get("phone"),
            )
        except AccountRecoveryValidationError as e:
            return Response({"detail": e.detail}, status=400)

        account = resolve_recovery_account(
            tenant=tenant,
            target=target,
            name=name,
            phone=phone,
        )
        if account is None:
            return Response({"message": GENERIC_MESSAGES[mode]}, status=200)

        try:
            if mode == "username":
                send_username_recovery(account)
            else:
                send_password_recovery(account)
        except AccountRecoveryValidationError as e:
            return Response({"detail": e.detail}, status=400)
        except AccountRecoveryDeliveryError as e:
            return Response({"detail": e.detail}, status=503)

        return Response({"message": GENERIC_MESSAGES[mode]}, status=200)
