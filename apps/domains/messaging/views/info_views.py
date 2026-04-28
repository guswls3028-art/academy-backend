# apps/support/messaging/views/info_views.py
"""
메시징 정보/설정 관련 뷰 — 잔액, 충전, 채널 확인, 발신번호 인증, 연동 테스트
"""

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.messaging.models import MessageTemplate
from apps.domains.messaging.credit_services import (
    charge_credits as do_charge,
    get_tenant_messaging_info,
)
from apps.domains.messaging.serializers import (
    MessagingInfoSerializer,
    MessagingInfoUpdateSerializer,
    ChargeRequestSerializer,
    VerifySenderRequestSerializer,
)
from apps.domains.messaging.solapi_sender_client import verify_sender_number
from apps.domains.messaging.policy import can_send_sms, resolve_kakao_channel
from apps.domains.messaging.selectors import has_any_approved_template


class MessagingInfoView(APIView):
    """GET: 현재 테넌트 메시징 정보. PATCH: PFID 저장"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = request.tenant
        serializer = MessagingInfoSerializer(tenant)
        data = serializer.data
        # 정책 SSOT 기반: 발송 허용·채널 출처 (API 응답만 사용, 프론트에서 재계산 금지)
        data["sms_allowed"] = can_send_sms(tenant.id)
        channel = resolve_kakao_channel(tenant.id)
        data["channel_source"] = "system_default" if channel.get("use_default", True) else "tenant_override"
        resolved_pf_id = (channel.get("pf_id") or "").strip()
        data["resolved_pf_id"] = resolved_pf_id
        # alimtalk_available: PFID resolved AND at least one APPROVED template exists (tenant or owner fallback)
        data["alimtalk_available"] = bool(resolved_pf_id and has_any_approved_template(tenant.id))
        return Response(data)

    def patch(self, request):
        tenant = request.tenant
        ser = MessagingInfoUpdateSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        update_fields = []
        if ser.validated_data.get("kakao_pfid") is not None:
            tenant.kakao_pfid = (ser.validated_data["kakao_pfid"] or "").strip()
            update_fields.append("kakao_pfid")
        if ser.validated_data.get("messaging_sender") is not None:
            tenant.messaging_sender = (
                ser.validated_data["messaging_sender"] or ""
            ).strip().replace("-", "")
            update_fields.append("messaging_sender")
        if ser.validated_data.get("messaging_provider") is not None:
            tenant.messaging_provider = ser.validated_data["messaging_provider"]
            update_fields.append("messaging_provider")
        # 자체 연동 키
        for field in ("own_solapi_api_key", "own_solapi_api_secret", "own_ppurio_api_key", "own_ppurio_account"):
            if field in ser.validated_data:
                setattr(tenant, field, (ser.validated_data[field] or "").strip())
                update_fields.append(field)
        if update_fields:
            tenant.save(update_fields=update_fields)
        serializer = MessagingInfoSerializer(tenant)
        data = serializer.data
        data["sms_allowed"] = can_send_sms(tenant.id)
        channel = resolve_kakao_channel(tenant.id)
        data["channel_source"] = "system_default" if channel.get("use_default", True) else "tenant_override"
        resolved_pf_id = (channel.get("pf_id") or "").strip()
        data["resolved_pf_id"] = resolved_pf_id
        data["alimtalk_available"] = bool(resolved_pf_id and has_any_approved_template(tenant.id))
        return Response(data)


class ChargeView(APIView):
    """POST: 크레딧 충전 (결제 완료 후)"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        ser = ChargeRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        amount = ser.validated_data["amount"]
        try:
            new_balance = do_charge(request.tenant.id, amount)
            return Response({"credit_balance": str(new_balance)})
        except ValueError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )


class VerifySenderView(APIView):
    """
    POST: 입력한 발신번호가 솔라피에 등록·활성화된 번호인지 조회.
    - Body: { "phone_number": "01031217466" }
    - Response: { "verified": bool, "message": str }
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        from django.conf import settings

        ser = VerifySenderRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        phone = (ser.validated_data["phone_number"] or "").strip()

        tenant = request.tenant
        provider = (tenant.messaging_provider or "solapi").strip().lower()

        # 자체 연동 키 우선, 없으면 시스템 키
        if provider == "solapi" and tenant.own_solapi_api_key and tenant.own_solapi_api_secret:
            api_key = tenant.own_solapi_api_key
            api_secret = tenant.own_solapi_api_secret
        else:
            api_key = getattr(settings, "SOLAPI_API_KEY", None) or ""
            api_secret = getattr(settings, "SOLAPI_API_SECRET", None) or ""

        if provider == "ppurio":
            return Response(
                {"verified": True, "message": "뿌리오는 발신번호 인증을 뿌리오 관리자 페이지(ppurio.com)에서 직접 진행합니다. 여기서는 저장만 하시면 됩니다."},
                status=status.HTTP_200_OK,
            )

        if not api_key or not api_secret:
            return Response(
                {"verified": False, "message": "솔라피 API가 설정되지 않았습니다. 직접 연동 모드에서 API 키를 먼저 등록하세요."},
                status=status.HTTP_200_OK,
            )

        try:
            verified, message = verify_sender_number(api_key, api_secret, phone)
            return Response({"verified": verified, "message": message})
        except ValueError as e:
            return Response(
                {"verified": False, "message": str(e)},
                status=status.HTTP_200_OK,
            )
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("verify_sender unexpected error")
            return Response(
                {"verified": False, "message": f"인증 확인 중 오류: {str(e)}"},
                status=status.HTTP_200_OK,
            )


class ChannelCheckView(APIView):
    """GET: 채널 공유 확인 (파트너 등록 여부) — 4단계, 스텁 가능"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        channel = resolve_kakao_channel(request.tenant.id)
        resolved_pf_id = (channel.get("pf_id") or "").strip()
        if not resolved_pf_id:
            return Response({"shared": False, "message": "PFID 미연동 (테넌트·시스템 모두 없음)"})
        source = "시스템 기본" if channel.get("use_default", True) else "테넌트 직접 연동"
        return Response({"shared": True, "message": f"연동됨 ({source})"})


class TestCredentialsView(APIView):
    """POST: 현재 저장된 공급자 연동 키가 유효한지 테스트.
    테넌트 자체 키 또는 시스템 키를 검증하여 결과를 반환한다.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        import logging as _logging
        _logger = _logging.getLogger(__name__)
        from django.conf import settings

        tenant = request.tenant
        provider = (tenant.messaging_provider or "solapi").strip().lower()

        results = {"provider": provider, "checks": []}

        if provider == "solapi":
            # Solapi: 자체 키 우선, 없으면 시스템 키
            if tenant.own_solapi_api_key and tenant.own_solapi_api_secret:
                api_key = tenant.own_solapi_api_key
                api_secret = tenant.own_solapi_api_secret
                key_source = "tenant"
            else:
                api_key = getattr(settings, "SOLAPI_API_KEY", None) or ""
                api_secret = getattr(settings, "SOLAPI_API_SECRET", None) or ""
                key_source = "system"

            if not api_key or not api_secret:
                results["checks"].append({
                    "test": "api_credentials",
                    "ok": False,
                    "message": "솔라피 API 키가 설정되지 않았습니다." + (
                        " 직접 연동 모드에서 API Key와 Secret을 입력해 주세요."
                        if key_source == "tenant"
                        else " 운영자에게 문의하세요."
                    ),
                })
            else:
                # 발신번호 목록 조회로 인증 테스트
                try:
                    from apps.domains.messaging.solapi_sender_client import get_active_sender_numbers
                    numbers = get_active_sender_numbers(api_key, api_secret)
                    results["checks"].append({
                        "test": "api_credentials",
                        "ok": True,
                        "message": f"솔라피 API 인증 성공 ({key_source} 키). 등록된 발신번호 {len(numbers)}개.",
                        "sender_numbers": numbers[:10],
                    })
                except ValueError as e:
                    err_msg = str(e)
                    results["checks"].append({
                        "test": "api_credentials",
                        "ok": False,
                        "message": f"솔라피 API 인증 실패: {err_msg}",
                    })
                except Exception as e:
                    _logger.exception("test_credentials solapi error")
                    results["checks"].append({
                        "test": "api_credentials",
                        "ok": False,
                        "message": f"솔라피 연결 오류: {str(e)[:200]}",
                    })

            # 발신번호 확인
            sender = (tenant.messaging_sender or "").strip()
            if sender:
                results["checks"].append({
                    "test": "sender_number",
                    "ok": True,
                    "message": f"발신번호 등록됨: {sender}",
                })
            else:
                results["checks"].append({
                    "test": "sender_number",
                    "ok": False,
                    "message": "발신번호가 등록되지 않았습니다. SMS 발송에 필요합니다.",
                })

        elif provider == "ppurio":
            if tenant.own_ppurio_api_key and tenant.own_ppurio_account:
                api_key = tenant.own_ppurio_api_key
                account = tenant.own_ppurio_account
                key_source = "tenant"
            else:
                import os
                api_key = os.environ.get("PPURIO_API_KEY") or getattr(settings, "PPURIO_API_KEY", "")
                account = os.environ.get("PPURIO_ACCOUNT") or getattr(settings, "PPURIO_ACCOUNT", "")
                key_source = "system"

            if not api_key or not account:
                results["checks"].append({
                    "test": "api_credentials",
                    "ok": False,
                    "message": "뿌리오 API 키 또는 Account ID가 설정되지 않았습니다." + (
                        " 직접 연동 모드에서 입력해 주세요."
                        if key_source == "tenant"
                        else " 운영자에게 문의하세요."
                    ),
                })
            else:
                # 뿌리오: 토큰 발급 테스트
                try:
                    from apps.domains.messaging.ppurio_client import _get_access_token, DEFAULT_API_URL
                    creds = {"api_key": api_key, "account": account, "api_url": DEFAULT_API_URL}
                    token = _get_access_token(creds)
                    if token:
                        results["checks"].append({
                            "test": "api_credentials",
                            "ok": True,
                            "message": f"뿌리오 API 인증 성공 ({key_source} 키). 토큰 발급 확인됨.",
                        })
                    else:
                        results["checks"].append({
                            "test": "api_credentials",
                            "ok": False,
                            "message": "뿌리오 토큰 발급 실패. API Key 또는 Account ID를 확인해 주세요.",
                        })
                except Exception as e:
                    _logger.exception("test_credentials ppurio error")
                    results["checks"].append({
                        "test": "api_credentials",
                        "ok": False,
                        "message": f"뿌리오 연결 오류: {str(e)[:200]}",
                    })

            sender = (tenant.messaging_sender or "").strip()
            if sender:
                results["checks"].append({
                    "test": "sender_number",
                    "ok": True,
                    "message": f"발신번호 등록됨: {sender}",
                })
            else:
                results["checks"].append({
                    "test": "sender_number",
                    "ok": False,
                    "message": "발신번호가 등록되지 않았습니다.",
                })

        # 공통: 알림톡 채널 확인
        channel = resolve_kakao_channel(tenant.id)
        pf_id = (channel.get("pf_id") or "").strip()
        if pf_id:
            source = "자체 채널" if not channel.get("use_default") else "시스템 기본 채널"
            results["checks"].append({
                "test": "alimtalk_channel",
                "ok": True,
                "message": f"알림톡 채널 연동됨 ({source})",
            })
        else:
            results["checks"].append({
                "test": "alimtalk_channel",
                "ok": False,
                "message": "알림톡 채널(PFID)이 미연동입니다. 알림톡을 사용하려면 PFID를 등록해 주세요.",
            })

        # 승인된 템플릿 수
        approved_count = MessageTemplate.objects.filter(
            tenant=tenant, solapi_status="APPROVED"
        ).count()
        results["checks"].append({
            "test": "approved_templates",
            "ok": approved_count > 0,
            "message": f"검수 승인된 템플릿: {approved_count}개" + (
                "" if approved_count > 0 else " (알림톡 발송에 필요합니다)"
            ),
        })

        all_ok = all(c["ok"] for c in results["checks"])
        results["all_ok"] = all_ok
        results["summary"] = (
            "모든 설정이 정상입니다. 메시지를 발송할 수 있습니다."
            if all_ok
            else "일부 설정이 필요합니다. 위 항목을 확인해 주세요."
        )

        return Response(results)
