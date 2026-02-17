# apps/support/messaging/views.py
"""
메시징 API — 잔액/충전/PFID/발송 로그 (테넌트 기준)
"""

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.core.models import Tenant
from apps.support.messaging.models import NotificationLog, MessageTemplate
from apps.support.messaging.solapi_template_client import (
    create_kakao_template,
    validate_template_variables,
)
from apps.support.messaging.credit_services import (
    charge_credits as do_charge,
    get_tenant_messaging_info,
)
from apps.support.messaging.serializers import (
    MessagingInfoSerializer,
    MessagingInfoUpdateSerializer,
    ChargeRequestSerializer,
    NotificationLogSerializer,
    MessageTemplateSerializer,
    SendMessageRequestSerializer,
)


class MessagingInfoView(APIView):
    """GET: 현재 테넌트 메시징 정보. PATCH: PFID 저장"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = request.tenant
        serializer = MessagingInfoSerializer(tenant)
        data = serializer.data
        data["credit_balance"] = str(data["credit_balance"])
        data["base_price"] = str(data["base_price"])
        return Response(data)

    def patch(self, request):
        tenant = request.tenant
        ser = MessagingInfoUpdateSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        pfid = ser.validated_data.get("kakao_pfid")
        if pfid is not None:
            tenant.kakao_pfid = (pfid or "").strip()
            tenant.save(update_fields=["kakao_pfid"])
        serializer = MessagingInfoSerializer(tenant)
        data = serializer.data
        data["credit_balance"] = str(data["credit_balance"])
        data["base_price"] = str(data["base_price"])
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


class NotificationLogListView(APIView):
    """GET: 발송 로그 목록 (페이지네이션)"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        page = max(1, int(request.query_params.get("page", 1)))
        page_size = min(50, max(1, int(request.query_params.get("page_size", 20))))
        offset = (page - 1) * page_size
        qs = (
            NotificationLog.objects.filter(tenant=request.tenant)
            .order_by("-sent_at")[offset : offset + page_size]
        )
        count = NotificationLog.objects.filter(tenant=request.tenant).count()
        items = [
            {
                "id": r.id,
                "sent_at": r.sent_at,
                "success": r.success,
                "amount_deducted": r.amount_deducted,
                "recipient_summary": r.recipient_summary or "",
                "template_summary": r.template_summary or "",
                "failure_reason": r.failure_reason or "",
            }
            for r in qs
        ]
        return Response({"results": items, "count": count})


class ChannelCheckView(APIView):
    """GET: 채널 공유 확인 (파트너 등록 여부) — 4단계, 스텁 가능"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        # TODO: Solapi/카카오 API로 실제 채널 공유 여부 조회
        pfid = (request.tenant.kakao_pfid or "").strip()
        if not pfid:
            return Response({"shared": False, "message": "PFID 미연동"})
        return Response({"shared": True, "message": "연동됨 (실제 검증은 API 연동 후)"})


class MessageTemplateListCreateView(APIView):
    """GET: 템플릿 목록 (category 쿼리로 필터). POST: 템플릿 생성"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        qs = MessageTemplate.objects.filter(tenant=request.tenant).order_by("-updated_at")
        category = (request.query_params.get("category") or "").strip().lower()
        if category in ("default", "lecture", "clinic"):
            qs = qs.filter(category=category)
        serializer = MessageTemplateSerializer(qs, many=True)
        return Response(serializer.data)

    def post(self, request):
        serializer = MessageTemplateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(tenant=request.tenant)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class MessageTemplateDetailView(APIView):
    """GET/PATCH/DELETE: 단일 템플릿"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def _get_template(self, request, pk):
        return MessageTemplate.objects.filter(tenant=request.tenant, pk=pk).first()

    def get(self, request, pk):
        t = self._get_template(request, pk)
        if not t:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(MessageTemplateSerializer(t).data)

    def patch(self, request, pk):
        t = self._get_template(request, pk)
        if not t:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = MessageTemplateSerializer(t, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, pk):
        t = self._get_template(request, pk)
        if not t:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        t.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MessageTemplateSubmitReviewView(APIView):
    """
    POST: 해당 템플릿을 솔라피에 알림톡 템플릿으로 등록(검수 신청).
    - 테넌트 PFID 사용
    - #{변수명} 검증 후 솔라피 API 호출
    - 응답 templateId 및 PENDING 상태 DB 저장
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, pk):
        from django.conf import settings

        t = MessageTemplate.objects.filter(tenant=request.tenant, pk=pk).first()
        if not t:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        pfid = (request.tenant.kakao_pfid or "").strip()
        if not pfid:
            return Response(
                {"detail": "카카오 채널(PFID)이 연동되지 않았습니다. 메시징 설정에서 PFID를 등록해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        api_key = getattr(settings, "SOLAPI_API_KEY", None) or ""
        api_secret = getattr(settings, "SOLAPI_API_SECRET", None) or ""
        if not api_key or not api_secret:
            return Response(
                {"detail": "솔라피 API 키가 설정되지 않았습니다. (SOLAPI_API_KEY, SOLAPI_API_SECRET)"},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # 변수 형식 검증 (본문 + 제목)
        ok, errs = validate_template_variables(t.body, t.subject or "")
        if not ok:
            return Response(
                {"detail": "변수 검증 실패: " + "; ".join(errs)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 알림톡 content: 제목 + 본문 (제목이 있으면 첫 줄로)
        content = (t.subject.strip() + "\n" + t.body).strip() if t.subject else t.body

        try:
            result = create_kakao_template(
                api_key=api_key,
                api_secret=api_secret,
                channel_id=pfid,
                name=t.name,
                content=content,
                category_code="TE",
            )
            template_id = result.get("templateId", "")
        except ValueError as e:
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        t.solapi_template_id = template_id
        t.solapi_status = "PENDING"
        t.save(update_fields=["solapi_template_id", "solapi_status", "updated_at"])

        serializer = MessageTemplateSerializer(t)
        return Response(
            {"detail": "검수 신청이 완료되었습니다. 카카오 검수는 영업일 기준 1~3일 소요됩니다.", "template": serializer.data},
            status=status.HTTP_200_OK,
        )
