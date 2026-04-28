# apps/support/messaging/views/send_views.py
"""
메시지 발송 뷰 — 학생/학부모/직원 대상 수동 발송
"""

import re
from datetime import timedelta

from django.utils import timezone

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.messaging.models import NotificationLog, MessageTemplate
from apps.domains.messaging.serializers import SendMessageRequestSerializer
from apps.domains.messaging.selectors import resolve_freeform_template


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

        from apps.domains.messaging.services import enqueue_sms, get_site_url, get_tenant_site_url
        from apps.domains.messaging.policy import MessagingPolicyError

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
                from apps.domains.messaging.policy import get_owner_tenant_id
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
            from apps.domains.messaging.alimtalk_content_builders import (
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
                from apps.domains.messaging.alimtalk_content_builders import TEMPLATE_TYPE_TO_SOLAPI_ID, TYPE_SCORE
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
        enqueue_failed = 0
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
            text = re.sub(r"#\{[^}]+\}", "", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
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
                    event_type="manual_send",
                    target_type="student" if send_to != "parent" else "parent",
                    target_id=s.id,
                    target_name=name,
                )
            except MessagingPolicyError as e:
                return Response(
                    {"detail": str(e) or "문자(SMS) 발송은 내 테넌트에서만 가능합니다."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if ok:
                enqueued += 1
            else:
                enqueue_failed += 1

        detail = f"발송 예정 {enqueued}건"
        if enqueue_failed:
            detail += f" (큐 등록 실패 {enqueue_failed}건)"
        if skipped_no_phone:
            detail += f" (전화번호 없음 {skipped_no_phone}건)"
        return Response({
            "detail": detail + ".",
            "enqueued": enqueued,
            "enqueue_failed": enqueue_failed,
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
                from apps.domains.messaging.policy import get_owner_tenant_id
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
                from apps.domains.messaging.alimtalk_content_builders import get_unified_for_category
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

        from apps.domains.messaging.services import enqueue_sms, get_site_url, get_tenant_site_url
        from apps.domains.messaging.policy import MessagingPolicyError

        enqueued = 0
        skipped_no_phone = 0
        enqueue_failed = 0
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
            text = re.sub(r"#\{[^}]+\}", "", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if subject_base:
                text = subject_base + "\n" + text

            alimtalk_replacements = None
            template_id_solapi = None
            if message_mode == "alimtalk" and solapi_template_id:
                template_id_solapi = solapi_template_id
                if use_unified and unified_template_type:
                    from apps.domains.messaging.alimtalk_content_builders import build_manual_replacements
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
                    event_type="manual_send_staff",
                    target_type="staff",
                    target_id=s.id,
                    target_name=name,
                )
            except MessagingPolicyError as e:
                return Response(
                    {"detail": str(e) or "문자(SMS) 발송은 내 테넌트에서만 가능합니다."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if ok:
                enqueued += 1
            else:
                enqueue_failed += 1

        detail = f"발송 예정 {enqueued}건"
        if enqueue_failed:
            detail += f" (큐 등록 실패 {enqueue_failed}건)"
        if skipped_no_phone:
            detail += f" (전화번호 없음 {skipped_no_phone}건)"
        return Response({
            "detail": detail + ".",
            "enqueued": enqueued,
            "enqueue_failed": enqueue_failed,
            "skipped_no_phone": skipped_no_phone,
        }, status=status.HTTP_200_OK)
