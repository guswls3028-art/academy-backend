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
from apps.support.messaging.models import NotificationLog, MessageTemplate, AutoSendConfig
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
    VerifySenderRequestSerializer,
    AutoSendConfigSerializer,
    AutoSendConfigUpdateSerializer,
)
from apps.support.messaging.solapi_sender_client import verify_sender_number
from apps.support.messaging.policy import can_send_sms, resolve_kakao_channel


class MessagingInfoView(APIView):
    """GET: 현재 테넌트 메시징 정보. PATCH: PFID 저장"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = request.tenant
        serializer = MessagingInfoSerializer(tenant)
        data = serializer.data
        data["credit_balance"] = str(data["credit_balance"])
        data["base_price"] = str(data["base_price"])
        # 정책 SSOT 기반: 발송 허용·채널 출처 (API 응답만 사용, 프론트에서 재계산 금지)
        data["sms_allowed"] = can_send_sms(tenant.id)
        channel = resolve_kakao_channel(tenant.id)
        data["channel_source"] = "system_default" if channel.get("use_default", True) else "tenant_override"
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
        data["credit_balance"] = str(data["credit_balance"])
        data["base_price"] = str(data["base_price"])
        data["sms_allowed"] = can_send_sms(tenant.id)
        channel = resolve_kakao_channel(tenant.id)
        data["channel_source"] = "system_default" if channel.get("use_default", True) else "tenant_override"
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
                "message_body": r.message_body or "",
                "message_mode": r.message_mode or "",
            }
            for r in qs
        ]
        return Response({"results": items, "count": count})


class NotificationLogDetailView(APIView):
    """GET: 발송 로그 단건 상세"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request, pk):
        log = NotificationLog.objects.filter(tenant=request.tenant, pk=pk).first()
        if not log:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({
            "id": log.id,
            "sent_at": log.sent_at,
            "success": log.success,
            "amount_deducted": log.amount_deducted,
            "recipient_summary": log.recipient_summary or "",
            "template_summary": log.template_summary or "",
            "failure_reason": log.failure_reason or "",
            "message_body": log.message_body or "",
            "message_mode": log.message_mode or "",
        })


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

        api_key = getattr(settings, "SOLAPI_API_KEY", None) or ""
        api_secret = getattr(settings, "SOLAPI_API_SECRET", None) or ""
        if not api_key or not api_secret:
            return Response(
                {"verified": False, "message": "솔라피 API가 설정되지 않았습니다."},
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
        valid_cats = {c.value for c in MessageTemplate.Category}
        if category and category in valid_cats:
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


class SendMessageView(APIView):
    """
    POST: 선택 학생(들) 또는 직원(들)에게 메시지 발송 (SQS enqueue → 워커가 Solapi 발송).
    - student_ids + send_to "student"|"parent": 학생/학부모 전화로 발송
    - staff_ids + send_to "staff": 직원 전화로 발송
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        ser = SendMessageRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        tenant = request.tenant
        send_to = data["send_to"]
        message_mode = (data.get("message_mode") or "sms").strip().lower()
        if message_mode not in ("sms", "alimtalk", "both"):
            message_mode = "sms"
        template_id = data.get("template_id")
        raw_body = (data.get("raw_body") or "").strip()
        raw_subject = (data.get("raw_subject") or "").strip()

        # 발신번호 없으면 워커에서 sender_required 로 조용히 실패함 → API에서 즉시 400
        sender = (tenant.messaging_sender or "").strip()
        if not sender:
            return Response(
                {
                    "detail": "발신번호가 등록되지 않았습니다. 메시지 > 설정 탭에서 발신번호를 등록·저장한 뒤 발송해 주세요.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.support.messaging.services import enqueue_sms, get_site_url
        from apps.support.messaging.policy import MessagingPolicyError

        if send_to == "staff":
            return self._send_to_staff(
                request, tenant, data, sender, message_mode,
                template_id, raw_body, raw_subject,
            )

        # 학생/학부모 수신
        student_ids = data.get("student_ids") or []
        from apps.domains.students.models import Student

        students = list(
            Student.objects.filter(tenant=tenant, id__in=student_ids, deleted_at__isnull=True).only(
                "id", "name", "phone", "parent_phone"
            )
        )
        if not students:
            return Response(
                {"detail": "선택한 학생을 찾을 수 없거나 삭제된 학생입니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        body_base = (raw_body or "").strip()
        subject_base = (raw_subject or "").strip()
        t = None
        solapi_template_id = ""
        if template_id:
            t = MessageTemplate.objects.filter(tenant=tenant, pk=template_id).first()
            if not t:
                return Response(
                    {"detail": "템플릿을 찾을 수 없습니다."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if not body_base:
                body_base = (t.body or "").strip()
            if not subject_base:
                subject_base = (t.subject or "").strip()
            solapi_template_id = (t.solapi_template_id or "").strip()

        if message_mode in ("alimtalk", "both") and (not solapi_template_id or (t and getattr(t, "solapi_status", None) != "APPROVED")):
            return Response(
                {"detail": "알림톡/폴백 모드는 검수 승인된 템플릿이 필요합니다. 템플릿을 선택하거나 SMS만 모드로 발송하세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not body_base:
            return Response(
                {"detail": "발송할 본문이 비어 있습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        enqueued = 0
        skipped_no_phone = 0
        for s in students:
            phone = None
            if send_to == "student":
                phone = (s.phone or "").replace("-", "").strip()
            else:
                phone = (s.parent_phone or "").replace("-", "").strip()
            if not phone or len(phone) < 10:
                skipped_no_phone += 1
                continue
            name = (s.name or "").strip()
            name_2 = name[:2] if len(name) >= 2 else name
            name_3 = name[:3] if len(name) >= 3 else name
            site_url = get_site_url(request) or ""
            text = (
                body_base.replace("#{student_name_2}", name_2)
                .replace("#{student_name_3}", name_3)
                .replace("#{site_link}", site_url)
            )
            if subject_base:
                text = subject_base + "\n" + text

            alimtalk_replacements = None
            template_id_solapi = None
            if message_mode in ("alimtalk", "both") and solapi_template_id:
                template_id_solapi = solapi_template_id
                alimtalk_replacements = [
                    {"key": "student_name_2", "value": name_2},
                    {"key": "student_name_3", "value": name_3},
                    {"key": "site_link", "value": site_url},
                ]

            try:
                ok = enqueue_sms(
                    tenant_id=tenant.id,
                    to=phone,
                    text=text,
                    sender=sender,
                    message_mode=message_mode,
                    template_id=template_id_solapi,
                    alimtalk_replacements=alimtalk_replacements,
                )
            except MessagingPolicyError as e:
                return Response(
                    {"detail": str(e) or "문자(SMS) 발송은 내 테넌트에서만 가능합니다."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if ok:
                enqueued += 1

        return Response({
            "detail": f"발송 예정 {enqueued}건입니다.",
            "enqueued": enqueued,
            "skipped_no_phone": skipped_no_phone,
        }, status=status.HTTP_200_OK)

    def _send_to_staff(
        self, request, tenant, data, sender, message_mode,
        template_id, raw_body, raw_subject,
    ):
        from apps.domains.staffs.models import Staff
        staff_ids = data.get("staff_ids") or []
        staffs = list(
            Staff.objects.filter(tenant=tenant, id__in=staff_ids).only("id", "name", "phone")
        )
        if not staffs:
            return Response(
                {"detail": "선택한 직원을 찾을 수 없습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        body_base = (raw_body or "").strip()
        subject_base = (raw_subject or "").strip()
        t = None
        solapi_template_id = ""
        if template_id:
            t = MessageTemplate.objects.filter(tenant=tenant, pk=template_id).first()
            if t:
                if not body_base:
                    body_base = (t.body or "").strip()
                if not subject_base:
                    subject_base = (t.subject or "").strip()
                solapi_template_id = (t.solapi_template_id or "").strip()

        if message_mode in ("alimtalk", "both") and (not solapi_template_id or (t and getattr(t, "solapi_status", None) != "APPROVED")):
            return Response(
                {"detail": "알림톡/폴백 모드는 검수 승인된 템플릿이 필요합니다. 템플릿을 선택하거나 SMS만 모드로 발송하세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not body_base:
            return Response(
                {"detail": "발송할 본문이 비어 있습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.support.messaging.services import enqueue_sms, get_site_url
        from apps.support.messaging.policy import MessagingPolicyError

        enqueued = 0
        skipped_no_phone = 0
        for s in staffs:
            phone = (s.phone or "").replace("-", "").strip()
            if not phone or len(phone) < 10:
                skipped_no_phone += 1
                continue
            name = (s.name or "").strip()
            name_2 = name[:2] if len(name) >= 2 else name
            name_3 = name[:3] if len(name) >= 3 else name
            site_url = get_site_url(request) or ""
            text = (
                body_base.replace("#{student_name_2}", name_2)
                .replace("#{student_name_3}", name_3)
                .replace("#{site_link}", site_url)
            )
            if subject_base:
                text = subject_base + "\n" + text

            alimtalk_replacements = None
            template_id_solapi = None
            if message_mode in ("alimtalk", "both") and solapi_template_id:
                template_id_solapi = solapi_template_id
                alimtalk_replacements = [
                    {"key": "student_name_2", "value": name_2},
                    {"key": "student_name_3", "value": name_3},
                    {"key": "site_link", "value": site_url},
                ]

            try:
                ok = enqueue_sms(
                    tenant_id=tenant.id,
                    to=phone,
                    text=text,
                    sender=sender,
                    message_mode=message_mode,
                    template_id=template_id_solapi,
                    alimtalk_replacements=alimtalk_replacements,
                )
            except MessagingPolicyError as e:
                return Response(
                    {"detail": str(e) or "문자(SMS) 발송은 내 테넌트에서만 가능합니다."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if ok:
                enqueued += 1

        return Response({
            "detail": f"발송 예정 {enqueued}건입니다.",
            "enqueued": enqueued,
            "skipped_no_phone": skipped_no_phone,
        }, status=status.HTTP_200_OK)


class AutoSendConfigView(APIView):
    """
    GET: 테넌트의 모든 자동발송 설정 목록 (트리거별)
    PATCH: 트리거별 설정 수정. Body: { "configs": [ { "trigger": "...", "template_id": null|int, "enabled": bool, "message_mode": "sms"|"alimtalk"|"both" }, ... ] }
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = request.tenant
        triggers = [c[0] for c in AutoSendConfig.Trigger.choices]
        configs = AutoSendConfig.objects.filter(tenant=tenant).select_related("template")
        by_trigger = {c.trigger: c for c in configs}

        result = []
        for trigger in triggers:
            c = by_trigger.get(trigger)
            if c:
                result.append(AutoSendConfigSerializer(c).data)
            else:
                result.append({
                    "id": None,
                    "trigger": trigger,
                    "template": None,
                    "template_name": "",
                    "template_solapi_status": "",
                    "enabled": False,
                    "message_mode": "sms",
                    "minutes_before": None,
                    "created_at": None,
                    "updated_at": None,
                })
        return Response(result)

    def patch(self, request):
        tenant = request.tenant
        configs_data = request.data.get("configs") or []
        if not isinstance(configs_data, list):
            return Response(
                {"detail": "configs는 배열이어야 합니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        for item in configs_data:
            trigger = (item.get("trigger") or "").strip()
            if not trigger or trigger not in dict(AutoSendConfig.Trigger.choices):
                continue
            template_id = item.get("template_id")
            enabled = item.get("enabled", False)
            message_mode = (item.get("message_mode") or "sms").strip().lower()
            if message_mode not in ("sms", "alimtalk", "both"):
                message_mode = "sms"
            minutes_before = item.get("minutes_before")
            if minutes_before is not None:
                try:
                    minutes_before = max(0, int(minutes_before)) if minutes_before != "" else None
                except (TypeError, ValueError):
                    minutes_before = None

            config, _ = AutoSendConfig.objects.get_or_create(
                tenant=tenant,
                trigger=trigger,
                defaults={"enabled": False, "message_mode": "sms"},
            )
            if template_id:
                t = MessageTemplate.objects.filter(
                    tenant=tenant, pk=int(template_id)
                ).first()
                config.template = t
            else:
                config.template = None
            config.enabled = enabled
            config.message_mode = message_mode
            config.minutes_before = minutes_before
            config.save()

        configs = AutoSendConfig.objects.filter(tenant=tenant).select_related("template")
        return Response([AutoSendConfigSerializer(c).data for c in configs])


class ProvisionDefaultTemplatesView(APIView):
    """POST: 기본 템플릿 + 자동발송 config 일괄 생성/리셋.
    - 기존 기본 템플릿(이름이 DEFAULT_TEMPLATES와 동일)은 최신 기본값으로 리셋
    - 사용자가 새로 만든 템플릿은 그대로 유지
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        from .default_templates import DEFAULT_TEMPLATES

        tenant = request.tenant
        existing_configs = {
            c.trigger: c
            for c in AutoSendConfig.objects.filter(tenant=tenant).select_related("template")
        }
        default_names = {d["name"] for d in DEFAULT_TEMPLATES.values()}
        created_templates = 0
        created_configs = 0
        reset_templates = 0
        linked = 0

        for trigger, defaults in DEFAULT_TEMPLATES.items():
            tpl_name = defaults["name"]
            tpl_category = defaults["category"]
            tpl_subject = defaults.get("subject", "")
            tpl_body = defaults["body"]

            existing_tpl = MessageTemplate.objects.filter(
                tenant=tenant, name=tpl_name,
            ).first()

            if existing_tpl:
                # 기본 템플릿이면 본문·제목·카테고리를 최신 기본값으로 리셋
                changed = False
                if existing_tpl.category != tpl_category:
                    existing_tpl.category = tpl_category
                    changed = True
                if existing_tpl.subject != tpl_subject:
                    existing_tpl.subject = tpl_subject
                    changed = True
                if existing_tpl.body != tpl_body:
                    existing_tpl.body = tpl_body
                    changed = True
                if changed:
                    existing_tpl.save(update_fields=["category", "subject", "body", "updated_at"])
                    reset_templates += 1
                tpl = existing_tpl
            else:
                tpl = MessageTemplate.objects.create(
                    tenant=tenant,
                    name=tpl_name,
                    category=tpl_category,
                    subject=tpl_subject,
                    body=tpl_body,
                )
                created_templates += 1

            existing = existing_configs.get(trigger)
            if existing:
                if not existing.template_id:
                    existing.template = tpl
                    existing.save(update_fields=["template", "updated_at"])
                    linked += 1
            else:
                AutoSendConfig.objects.create(
                    tenant=tenant,
                    trigger=trigger,
                    template=tpl,
                    enabled=True,
                    message_mode="both",
                    minutes_before=defaults.get("minutes_before"),
                )
                created_configs += 1

        total_configs = AutoSendConfig.objects.filter(tenant=tenant).count()
        return Response({
            "created_templates": created_templates,
            "created_configs": created_configs,
            "reset_templates": reset_templates,
            "linked": linked,
            "total_templates": MessageTemplate.objects.filter(tenant=tenant).count(),
            "total_configs": total_configs,
        }, status=status.HTTP_200_OK)
