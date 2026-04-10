# apps/support/messaging/views.py
"""
메시징 API — 잔액/충전/PFID/발송 로그 (테넌트 기준)
"""

from datetime import timedelta

from django.utils import timezone

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
from apps.support.messaging.selectors import resolve_freeform_template, has_any_approved_template


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


class MessageTemplateListCreateView(APIView):
    """GET: 템플릿 목록 (category 쿼리로 필터). POST: 템플릿 생성"""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        qs = MessageTemplate.objects.filter(tenant=request.tenant).order_by("-updated_at")
        category = (request.query_params.get("category") or "").strip().lower()
        valid_cats = {c.value for c in MessageTemplate.Category}
        if category and category in valid_cats:
            qs = qs.filter(category=category)

        # include_system=true: 오너 테넌트의 승인 알림톡 템플릿을 시스템 기본으로 포함
        # (자체 PFID 없는 테넌트가 알림톡 발송 시 시스템 기본 채널+템플릿 사용)
        include_system = (request.query_params.get("include_system") or "").strip().lower() in ("true", "1")
        result = MessageTemplateSerializer(qs, many=True).data
        if include_system:
            from apps.support.messaging.policy import get_owner_tenant_id
            owner_id = get_owner_tenant_id()
            if int(request.tenant.id) != owner_id:
                system_qs = MessageTemplate.objects.filter(
                    tenant_id=owner_id,
                    solapi_status="APPROVED",
                ).exclude(
                    category="signup",
                ).order_by("-updated_at")
                if category and category in valid_cats:
                    system_qs = system_qs.filter(category=category)
                result = list(result) + MessageTemplateSerializer(system_qs, many=True).data
        return Response(result)

    def post(self, request):
        serializer = MessageTemplateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        # 사용자가 생성하는 템플릿은 항상 is_system=False
        serializer.save(tenant=request.tenant, is_system=False)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class MessageTemplateDetailView(APIView):
    """GET/PATCH/DELETE: 단일 템플릿. 시스템 양식은 수정/삭제 차단."""
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
        if t.is_system:
            return Response(
                {"detail": "시스템 기본 양식은 수정할 수 없습니다. '복제' 후 수정해 주세요."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = MessageTemplateSerializer(t, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        data = serializer.data
        # 변수 유효성 경고 (soft validation — 저장은 허용, 경고만 반환)
        body = (t.body or "")
        import re as _re
        used_vars = set(_re.findall(r"#\{([^}]+)\}", body))
        if used_vars:
            KNOWN_VARS = {
                "학원이름", "학원명", "학생이름", "학생이름2", "학생이름3",
                "사이트링크", "강의명", "차시명", "날짜", "시간", "장소",
                "클리닉장소", "클리닉날짜", "클리닉시간", "클리닉명",
                "클리닉기존일정", "클리닉변동사항", "클리닉수정자",
                "강의날짜", "강의시간", "시험명", "과제명", "성적", "시험성적",
                "클리닉합불", "납부금액", "청구월", "반이름",
                "공지내용", "내용", "선생님메모",
                # 가입용
                "학생아이디", "학생비밀번호", "학부모아이디", "학부모비밀번호",
                "비밀번호안내", "인증번호",
            }
            unknown = used_vars - KNOWN_VARS
            if unknown:
                data["warnings"] = [f"인식할 수 없는 변수: #{{{v}}} — 발송 시 빈 값으로 대체됩니다." for v in sorted(unknown)]
        return Response(data)

    def delete(self, request, pk):
        t = self._get_template(request, pk)
        if not t:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if t.is_system:
            return Response(
                {"detail": "시스템 기본 양식은 삭제할 수 없습니다."},
                status=status.HTTP_403_FORBIDDEN,
            )
        t.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class MessageTemplateSetDefaultView(APIView):
    """POST: 해당 템플릿을 해당 카테고리의 기본 양식으로 지정 (tenant+category당 1개)."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, pk):
        t = MessageTemplate.objects.filter(tenant=request.tenant, pk=pk).first()
        if not t:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        # 같은 tenant+category의 기존 기본 해제
        MessageTemplate.objects.filter(
            tenant=request.tenant, category=t.category, is_user_default=True,
        ).exclude(pk=pk).update(is_user_default=False)
        # 토글: 이미 기본이면 해제, 아니면 설정
        t.is_user_default = not t.is_user_default
        t.save(update_fields=["is_user_default"])
        return Response(MessageTemplateSerializer(t).data)


class MessageTemplateDuplicateView(APIView):
    """POST: 시스템/기존 양식을 복제하여 내 양식으로 저장."""
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, pk):
        src = MessageTemplate.objects.filter(tenant=request.tenant, pk=pk).first()
        if not src:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        # 요청에 name이 있으면 사용, 없으면 원본 이름 + " (복사본)"
        new_name = (request.data.get("name") or "").strip()
        if not new_name:
            new_name = f"{src.name} (복사본)"
        dup = MessageTemplate.objects.create(
            tenant=request.tenant,
            category=src.category,
            name=new_name,
            subject=src.subject,
            body=src.body,
            is_system=False,
            is_user_default=False,
        )
        return Response(MessageTemplateSerializer(dup).data, status=status.HTTP_201_CREATED)


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

        tenant = request.tenant
        provider = (tenant.messaging_provider or "solapi").strip().lower()

        if provider == "ppurio":
            return Response(
                {"detail": "뿌리오는 알림톡 템플릿 검수를 뿌리오 관리자 페이지(ppurio.com)에서 직접 진행해야 합니다. "
                           "승인된 템플릿 코드를 받은 뒤, 이 템플릿의 템플릿 ID 필드에 해당 코드를 입력해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # PFID: 테넌트 직접 연동 > 시스템 기본
        pfid = (tenant.kakao_pfid or "").strip()
        if not pfid:
            default_pf_id = (getattr(settings, "SOLAPI_KAKAO_PF_ID", None) or "").strip()
            pfid = default_pf_id
        if not pfid:
            return Response(
                {"detail": "카카오 채널(PFID)이 연동되지 않았습니다. 메시징 설정에서 PFID를 등록해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 자체 솔라피 키 우선, 없으면 시스템 키
        if tenant.own_solapi_api_key and tenant.own_solapi_api_secret:
            api_key = tenant.own_solapi_api_key
            api_secret = tenant.own_solapi_api_secret
        else:
            api_key = getattr(settings, "SOLAPI_API_KEY", None) or ""
            api_secret = getattr(settings, "SOLAPI_API_SECRET", None) or ""
        if not api_key or not api_secret:
            return Response(
                {"detail": "솔라피 API 키가 설정되지 않았습니다. 직접 연동 모드에서 API 키를 먼저 등록하세요."},
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
        message_mode = (data.get("message_mode") or "alimtalk").strip().lower()
        if message_mode not in ("sms", "alimtalk"):
            message_mode = "alimtalk"
        template_id = data.get("template_id")
        raw_body = (data.get("raw_body") or "").strip()
        raw_subject = (data.get("raw_subject") or "").strip()

        # 발신번호: 알림톡 전용이면 선택, SMS면 필수
        sender = (tenant.messaging_sender or "").strip()
        if not sender and message_mode != "alimtalk":
            return Response(
                {
                    "detail": "발신번호가 등록되지 않았습니다. 메시지 > 설정 탭에서 발신번호를 등록·저장한 뒤 발송해 주세요.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.support.messaging.services import enqueue_sms, get_site_url, get_tenant_site_url
        from apps.support.messaging.policy import MessagingPolicyError

        # Rate limit: max 500 messages per tenant per hour
        one_hour_ago = timezone.now() - timedelta(hours=1)
        recent_count = NotificationLog.objects.filter(
            tenant=tenant, sent_at__gte=one_hour_ago,
        ).count()
        if recent_count >= 500:
            return Response(
                {"detail": "시간당 발송 한도(500건)를 초과했습니다. 잠시 후 다시 시도해 주세요."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

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
        if len(students) > 200:
            return Response(
                {"detail": f"한 번에 최대 200명까지 발송할 수 있습니다. (선택: {len(students)}명)"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        body_base = (raw_body or "").strip()
        subject_base = (raw_subject or "").strip()
        t = None
        solapi_template_id = ""
        use_unified = False       # 통합 4종 템플릿 사용 여부
        unified_template_type = None  # score / attendance / clinic_info / clinic_change

        if template_id:
            t = MessageTemplate.objects.filter(tenant=tenant, pk=template_id).first()
            # 오너 테넌트의 승인 시스템 템플릿도 허용 (알림톡 기본 채널 폴백)
            if not t and message_mode == "alimtalk":
                from apps.support.messaging.policy import get_owner_tenant_id
                owner_id = get_owner_tenant_id()
                if int(tenant.id) != owner_id:
                    t = MessageTemplate.objects.filter(
                        tenant_id=owner_id, pk=template_id, solapi_status="APPROVED",
                    ).first()
            if not t:
                return Response(
                    {"detail": "템플릿을 찾을 수 없습니다."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            if not body_base:
                body_base = (t.body or "").strip()
            if not subject_base:
                subject_base = (t.subject or "").strip()

        # ── 알림톡: 통합 4종 템플릿 우선 사용 ──
        # 시스템 기본양식(signup: 가입승인/비번찾기)만 자체 Solapi 템플릿 유지
        alimtalk_extra_vars = data.get("alimtalk_extra_vars") or {}
        raw_per_student = data.get("alimtalk_extra_vars_per_student") or {}
        extra_vars_per_student = {}
        for k, v in raw_per_student.items():
            try:
                extra_vars_per_student[int(k)] = v if isinstance(v, dict) else {}
            except (ValueError, TypeError):
                pass

        if message_mode == "alimtalk":
            from apps.support.messaging.alimtalk_content_builders import (
                get_unified_for_category,
                build_manual_replacements,
                SYSTEM_TEMPLATE_CATEGORIES,
            )
            category = (t.category if t else "") or ""
            tpl_name = (t.name if t else "") or ""
            unified_tt, unified_sid = get_unified_for_category(category, tpl_name, alimtalk_extra_vars)

            if unified_tt and unified_sid:
                # 통합 4종 사용
                use_unified = True
                unified_template_type = unified_tt
                solapi_template_id = unified_sid
            elif category in SYSTEM_TEMPLATE_CATEGORIES and t:
                # 시스템 기본양식: 자체 Solapi 템플릿 유지
                solapi_template_id = (t.solapi_template_id or "").strip()
            else:
                # 카테고리 매핑 없음 → score로 fallback
                use_unified = True
                unified_template_type = "score"
                from apps.support.messaging.alimtalk_content_builders import TEMPLATE_TYPE_TO_SOLAPI_ID, TYPE_SCORE
                solapi_template_id = TEMPLATE_TYPE_TO_SOLAPI_ID.get(TYPE_SCORE, "")

        if message_mode == "alimtalk" and not solapi_template_id:
            return Response(
                {"detail": "알림톡 모드는 검수 승인된 템플릿이 필요합니다. 템플릿을 선택하거나 SMS 모드로 발송하세요."},
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
            name_2 = name[-2:] if len(name) >= 2 else name
            name_3 = name
            site_url = get_tenant_site_url(request.tenant) or ""
            academy_name = (tenant.name or "").strip()

            # 학생별 개별 변수 merge
            student_extra = extra_vars_per_student.get(s.id, {})
            merged_context = {**alimtalk_extra_vars, **student_extra}

            # SMS용 text: 변수 치환
            import re as _re
            text = (
                body_base.replace("#{학생이름}", name)
                .replace("#{학생이름2}", name_2)
                .replace("#{학생이름3}", name_3)
                .replace("#{학원명}", academy_name)
                .replace("#{학원이름}", academy_name)
                .replace("#{사이트링크}", site_url)
            )
            for var in ("강의명", "차시명", "시험명", "과제명", "클리닉명", "장소", "날짜", "시간",
                        "시험성적", "클리닉합불", "공지내용", "내용",
                        "클리닉장소", "클리닉날짜", "클리닉시간",
                        "클리닉기존일정", "클리닉변동사항", "클리닉수정자",
                        "강의날짜", "강의시간"):
                val = merged_context.get(var, "")
                text = text.replace(f"#{{{var}}}", val)
            text = _re.sub(r"#\{[^}]+\}", "", text)
            text = _re.sub(r"\n{3,}", "\n\n", text).strip()
            if subject_base:
                text = subject_base + "\n" + text

            alimtalk_replacements = None
            template_id_solapi = None

            if message_mode == "alimtalk" and solapi_template_id:
                template_id_solapi = solapi_template_id

                if use_unified and unified_template_type:
                    # ── 통합 4종: build_manual_replacements로 정확한 변수 세트 빌드 ──
                    alimtalk_replacements = build_manual_replacements(
                        template_type=unified_template_type,
                        content_body=body_base,
                        context=merged_context,
                        tenant_name=academy_name,
                        student_name=name,
                        site_url=site_url,
                    )
                else:
                    # ── 시스템 기본양식: 기존 방식 유지 (가입승인/비번 등) ──
                    alimtalk_replacements = [
                        {"key": "학생이름", "value": name},
                        {"key": "학생이름2", "value": name_2},
                        {"key": "학생이름3", "value": name_3},
                        {"key": "학원명", "value": academy_name},
                        {"key": "사이트링크", "value": site_url},
                    ]
                    for var_key, var_val in merged_context.items():
                        if var_val and var_key not in ("학생이름", "학생이름2", "학생이름3", "사이트링크"):
                            alimtalk_replacements.append({"key": var_key, "value": str(var_val)})

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
        if len(staffs) > 200:
            return Response(
                {"detail": f"한 번에 최대 200명까지 발송할 수 있습니다. (선택: {len(staffs)}명)"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        body_base = (raw_body or "").strip()
        subject_base = (raw_subject or "").strip()
        t = None
        solapi_template_id = ""
        user_custom_content = ""
        use_unified = False
        unified_template_type = None

        if template_id:
            t = MessageTemplate.objects.filter(tenant=tenant, pk=template_id).first()
            # 오너 테넌트의 승인 시스템 템플릿도 허용 (알림톡 기본 채널 폴백)
            if not t and message_mode == "alimtalk":
                from apps.support.messaging.policy import get_owner_tenant_id
                owner_id = get_owner_tenant_id()
                if int(tenant.id) != owner_id:
                    t = MessageTemplate.objects.filter(
                        tenant_id=owner_id, pk=template_id, solapi_status="APPROVED",
                    ).first()
            if not t:
                return Response(
                    {"detail": "템플릿을 찾을 수 없습니다."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            tpl_body = t.body or ""
            if body_base and ("#{공지내용}" in tpl_body or "#{내용}" in tpl_body):
                user_custom_content = body_base
            if not body_base:
                body_base = (t.body or "").strip()
            if not subject_base:
                subject_base = (t.subject or "").strip()
            solapi_template_id = (t.solapi_template_id or "").strip()
            if message_mode == "alimtalk":
                from apps.support.messaging.alimtalk_content_builders import get_unified_for_category
                unified_tt, unified_sid = get_unified_for_category(t.category, t.name, {})
                if unified_tt and unified_sid:
                    use_unified = True
                    unified_template_type = unified_tt
                    solapi_template_id = unified_sid

        # 알림톡 모드에서 템플릿 미선택 시, 자유양식 승인 템플릿 자동 선택 (테넌트 → 오너 fallback)
        if message_mode == "alimtalk" and not solapi_template_id:
            freeform = resolve_freeform_template(tenant.id)
            if freeform:
                t = freeform
                solapi_template_id = (freeform.solapi_template_id or "").strip()
                user_custom_content = body_base
                if not subject_base:
                    subject_base = (freeform.subject or "").strip()

        if message_mode == "alimtalk" and (not solapi_template_id or (t and getattr(t, "solapi_status", None) != "APPROVED")):
            return Response(
                {"detail": "알림톡 모드는 검수 승인된 템플릿이 필요합니다. 템플릿을 선택하거나 SMS 모드로 발송하세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not body_base:
            return Response(
                {"detail": "발송할 본문이 비어 있습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.support.messaging.services import enqueue_sms, get_site_url, get_tenant_site_url
        from apps.support.messaging.policy import MessagingPolicyError

        enqueued = 0
        skipped_no_phone = 0
        for s in staffs:
            phone = (s.phone or "").replace("-", "").strip()
            if not phone or len(phone) < 10:
                skipped_no_phone += 1
                continue
            name = (s.name or "").strip()
            name_2 = name[-2:] if len(name) >= 2 else name  # 성(첫 글자) 제외 = 이름만
            name_3 = name  # 전체 이름 (하위 호환: 기존 #{학생이름3} 치환)
            site_url = get_tenant_site_url(request.tenant) or ""
            academy_name = (tenant.name or "").strip()
            text = (
                body_base.replace("#{학생이름}", name)
                .replace("#{학생이름2}", name_2)
                .replace("#{학생이름3}", name_3)
                .replace("#{학원명}", academy_name)
                .replace("#{사이트링크}", site_url)
                .replace("#{공지내용}", user_custom_content)
                .replace("#{내용}", user_custom_content)
            )
            # 수동 발송: 잔여 변수 catch-all 제거
            import re as _re
            text = _re.sub(r"#\{[^}]+\}", "", text)
            text = _re.sub(r"\n{3,}", "\n\n", text).strip()
            if subject_base:
                text = subject_base + "\n" + text

            alimtalk_replacements = None
            template_id_solapi = None
            if message_mode == "alimtalk" and solapi_template_id:
                template_id_solapi = solapi_template_id
                if use_unified and unified_template_type:
                    from apps.support.messaging.alimtalk_content_builders import build_manual_replacements
                    alimtalk_replacements = build_manual_replacements(
                        template_type=unified_template_type,
                        content_body=body_base,
                        context={"내용": user_custom_content, "공지내용": user_custom_content},
                        tenant_name=academy_name,
                        student_name=name,
                        site_url=site_url,
                    )
                else:
                    alimtalk_replacements = [
                        {"key": "학생이름", "value": name},
                        {"key": "학생이름2", "value": name_2},
                        {"key": "학생이름3", "value": name_3},
                        {"key": "학원명", "value": academy_name},
                        {"key": "사이트링크", "value": site_url},
                    ]
                    if user_custom_content:
                        alimtalk_replacements.append({"key": "공지내용", "value": user_custom_content})
                        alimtalk_replacements.append({"key": "내용", "value": user_custom_content})
                        alimtalk_replacements.append({"key": "선생님메모", "value": user_custom_content})

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
    PATCH: 트리거별 설정 수정. Body: { "configs": [ { "trigger": "...", "template_id": null|int, "enabled": bool, "message_mode": "sms"|"alimtalk" }, ... ] }
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def get(self, request):
        tenant = request.tenant
        triggers = [c[0] for c in AutoSendConfig.Trigger.choices]
        configs = AutoSendConfig.objects.filter(tenant=tenant).select_related("template").defer("delay_mode", "delay_value")

        # ── 자동 프로비저닝: config가 하나도 없으면 기본 템플릿 + config 자동 생성 ──
        if not configs.exists():
            self._auto_provision(tenant)
            configs = AutoSendConfig.objects.filter(tenant=tenant).select_related("template").defer("delay_mode", "delay_value")

        from apps.support.messaging.policy import get_trigger_policy

        by_trigger = {c.trigger: c for c in configs}

        result = []
        for trigger in triggers:
            c = by_trigger.get(trigger)
            policy_mode = get_trigger_policy(trigger)
            if c:
                data = AutoSendConfigSerializer(c).data
                data["policy_mode"] = policy_mode
                result.append(data)
            else:
                result.append({
                    "id": None,
                    "trigger": trigger,
                    "template": None,
                    "template_name": "",
                    "template_subject": "",
                    "template_body": "",
                    "template_solapi_status": "",
                    "enabled": False,
                    "message_mode": "alimtalk",
                    "minutes_before": None,
                    "created_at": None,
                    "updated_at": None,
                    "policy_mode": policy_mode,
                })
        return Response(result)

    @staticmethod
    def _auto_provision(tenant):
        """기본 템플릿 + AutoSendConfig 자동 생성 (첫 접근 시 1회)"""
        from .default_templates import get_default_templates
        import logging
        logger = logging.getLogger(__name__)

        templates = get_default_templates(tenant.name or "학원")
        valid_triggers = {c[0] for c in AutoSendConfig.Trigger.choices}
        for trigger, defaults in templates.items():
            tpl_name = defaults["name"]
            tpl, created = MessageTemplate.objects.get_or_create(
                tenant=tenant,
                name=tpl_name,
                defaults={
                    "category": defaults["category"],
                    "subject": defaults.get("subject", ""),
                    "body": defaults["body"],
                    "is_system": True,
                },
            )
            # 기존 시스템 템플릿이 is_system=False이면 교정
            if not created and not tpl.is_system:
                tpl.is_system = True
                tpl.save(update_fields=["is_system"])
            # 자유양식 템플릿 등 유효한 트리거가 아니면 AutoSendConfig 생성 스킵
            if trigger not in valid_triggers:
                continue
            AutoSendConfig.objects.get_or_create(
                tenant=tenant,
                trigger=trigger,
                defaults={
                    "template": tpl,
                    "enabled": True,
                    "message_mode": "alimtalk",
                    "minutes_before": defaults.get("minutes_before"),
                },
            )
        logger.info("Auto-provisioned default templates for tenant %s", tenant.id)

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
            message_mode = (item.get("message_mode") or "alimtalk").strip().lower()
            if message_mode not in ("sms", "alimtalk", "both"):
                message_mode = "alimtalk"
            minutes_before = item.get("minutes_before")
            if minutes_before is not None:
                try:
                    minutes_before = max(0, int(minutes_before)) if minutes_before != "" else None
                except (TypeError, ValueError):
                    minutes_before = None

            config, _ = AutoSendConfig.objects.get_or_create(
                tenant=tenant,
                trigger=trigger,
                defaults={"enabled": False, "message_mode": "alimtalk"},
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

            # delay_mode / delay_value — 마이그레이션 전에도 안전 (hasattr 체크)
            if hasattr(config, "delay_mode"):
                delay_mode = (item.get("delay_mode") or "").strip().lower()
                if delay_mode in ("immediate", "delay_minutes", "scheduled_hour"):
                    config.delay_mode = delay_mode
                delay_value = item.get("delay_value")
                if delay_value is not None:
                    try:
                        config.delay_value = max(0, int(delay_value)) if delay_value != "" else None
                    except (TypeError, ValueError):
                        config.delay_value = None
                elif delay_mode == "immediate":
                    config.delay_value = None

            config.save()

        configs = AutoSendConfig.objects.filter(tenant=tenant).select_related("template").defer("delay_mode", "delay_value")
        return Response([AutoSendConfigSerializer(c).data for c in configs])


class ProvisionDefaultTemplatesView(APIView):
    """POST: 기본 템플릿 + 자동발송 config 일괄 생성/리셋.
    - 기존 기본 템플릿(이름이 DEFAULT_TEMPLATES와 동일)은 최신 기본값으로 리셋
    - 사용자가 새로 만든 템플릿은 그대로 유지
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        from .default_templates import get_default_templates

        tenant = request.tenant
        templates = get_default_templates(tenant.name or "학원")
        existing_configs = {
            c.trigger: c
            for c in AutoSendConfig.objects.filter(tenant=tenant).select_related("template").defer("delay_mode", "delay_value")
        }
        default_names = {d["name"] for d in templates.values()}
        created_templates = 0
        created_configs = 0
        reset_templates = 0
        linked = 0

        # 자유양식 템플릿 이름 변경 마이그레이션 (구 이름 → 신 이름)
        academy_name = tenant.name or "학원"
        _freeform_name_migrations = {
            f"[{academy_name}] 학원 안내": f"[{academy_name}] 공지사항 안내",
            f"[{academy_name}] 결제 안내": f"[{academy_name}] 수납 안내",
            f"[{academy_name}] 클리닉 안내": f"[{academy_name}] 보충수업 안내",
        }
        _old_to_new = {v: k for k, v in _freeform_name_migrations.items()}  # new → old

        for trigger, defaults in templates.items():
            tpl_name = defaults["name"]
            tpl_category = defaults["category"]
            tpl_subject = defaults.get("subject", "")
            tpl_body = defaults["body"]

            existing_tpl = MessageTemplate.objects.filter(
                tenant=tenant, name=tpl_name,
            ).first()

            # 이름 변경된 자유양식: 구 이름으로도 검색하여 rename
            if not existing_tpl and tpl_name in _old_to_new:
                old_name = _old_to_new[tpl_name]
                existing_tpl = MessageTemplate.objects.filter(
                    tenant=tenant, name=old_name,
                ).first()
                if existing_tpl:
                    existing_tpl.name = tpl_name
                    existing_tpl.save(update_fields=["name", "updated_at"])

            if existing_tpl:
                # 기존 시스템 템플릿이 is_system=False이면 교정
                if not existing_tpl.is_system:
                    existing_tpl.is_system = True
                    existing_tpl.save(update_fields=["is_system"])
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
                    update_fields = ["category", "subject", "body", "updated_at"]
                    # 자유양식 템플릿의 본문이 변경되면 솔라피 연동 상태 초기화
                    # (구 본문으로 검수 중/승인된 템플릿 ID가 새 본문과 불일치 → 3034 에러 방지)
                    if trigger.startswith("freeform_") and existing_tpl.solapi_template_id:
                        existing_tpl.solapi_template_id = ""
                        existing_tpl.solapi_status = ""
                        update_fields.extend(["solapi_template_id", "solapi_status"])
                    existing_tpl.save(update_fields=update_fields)
                    reset_templates += 1
                tpl = existing_tpl
            else:
                tpl = MessageTemplate.objects.create(
                    tenant=tenant,
                    name=tpl_name,
                    category=tpl_category,
                    subject=tpl_subject,
                    body=tpl_body,
                    is_system=True,
                )
                created_templates += 1

            # 자유양식 템플릿 등 유효한 트리거가 아니면 AutoSendConfig 스킵
            valid_triggers = {c[0] for c in AutoSendConfig.Trigger.choices}
            if trigger not in valid_triggers:
                continue

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
                    message_mode="alimtalk",
                    minutes_before=defaults.get("minutes_before"),
                )
                created_configs += 1

        total_configs = AutoSendConfig.objects.filter(tenant=tenant).count()

        # ── 자유양식(freeform_*) 템플릿 자동 검수 신청 ──
        # PFID + API 키가 준비된 경우에만 솔라피에 등록(카카오 검수 대기)
        from django.conf import settings
        import logging as _provision_log
        _plog = _provision_log.getLogger(__name__)
        submitted_reviews = 0
        review_errors = []

        pfid = (tenant.kakao_pfid or "").strip()
        if not pfid:
            default_pf_id = (getattr(settings, "SOLAPI_KAKAO_PF_ID", None) or "").strip()
            pfid = default_pf_id

        if tenant.own_solapi_api_key and tenant.own_solapi_api_secret:
            r_api_key = tenant.own_solapi_api_key
            r_api_secret = tenant.own_solapi_api_secret
        else:
            r_api_key = getattr(settings, "SOLAPI_API_KEY", None) or ""
            r_api_secret = getattr(settings, "SOLAPI_API_SECRET", None) or ""

        can_submit_review = bool(pfid and r_api_key and r_api_secret)
        provider = (tenant.messaging_provider or "solapi").strip().lower()

        if can_submit_review and provider == "solapi":
            freeform_triggers = [k for k in templates.keys() if k.startswith("freeform_")]
            for trigger_key in freeform_triggers:
                tpl_name = templates[trigger_key]["name"]
                tpl_obj = MessageTemplate.objects.filter(tenant=tenant, name=tpl_name).first()
                if not tpl_obj:
                    continue
                # 이미 신청됐고 반려가 아니면 스킵
                if tpl_obj.solapi_template_id and tpl_obj.solapi_status in ("PENDING", "APPROVED"):
                    continue
                try:
                    content = tpl_obj.body.strip()
                    result = create_kakao_template(
                        api_key=r_api_key,
                        api_secret=r_api_secret,
                        channel_id=pfid,
                        name=tpl_obj.name,
                        content=content,
                        category_code="TE",
                    )
                    tpl_obj.solapi_template_id = result.get("templateId", "")
                    tpl_obj.solapi_status = "PENDING"
                    tpl_obj.save(update_fields=["solapi_template_id", "solapi_status", "updated_at"])
                    submitted_reviews += 1
                    _plog.info(
                        "Auto-submitted freeform template for review: tenant=%s name=%s templateId=%s",
                        tenant.id, tpl_obj.name, tpl_obj.solapi_template_id,
                    )
                except (ValueError, Exception) as e:
                    err_msg = f"{tpl_obj.name}: {str(e)[:200]}"
                    review_errors.append(err_msg)
                    _plog.warning("Auto-submit failed: tenant=%s %s", tenant.id, err_msg)

        return Response({
            "created_templates": created_templates,
            "created_configs": created_configs,
            "reset_templates": reset_templates,
            "linked": linked,
            "total_templates": MessageTemplate.objects.filter(tenant=tenant).count(),
            "total_configs": total_configs,
            "submitted_reviews": submitted_reviews,
            "review_errors": review_errors,
            "review_note": (
                "자유양식 템플릿 검수 신청이 완료되었습니다. 카카오 검수는 영업일 1~3일 소요됩니다."
                if submitted_reviews > 0
                else ("PFID 또는 API 키가 미설정이어서 검수 신청을 건너뛰었습니다." if not can_submit_review else "")
            ),
        }, status=status.HTTP_200_OK)


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
                    from apps.support.messaging.solapi_sender_client import get_active_sender_numbers
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
                    from apps.support.messaging.ppurio_client import _get_access_token, DEFAULT_API_URL
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
