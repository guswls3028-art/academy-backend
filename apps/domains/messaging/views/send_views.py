# apps/support/messaging/views/send_views.py
"""
메시지 발송 뷰 — 학생/학부모/직원 대상 수동 발송
"""

import re
from datetime import timedelta

from django.apps import apps as django_apps
from django.utils import timezone

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.messaging.models import NotificationLog, MessageTemplate
from apps.domains.messaging.permissions import can_send_messages
from apps.domains.messaging.serializers import SendMessageRequestSerializer
from apps.domains.messaging.selectors import resolve_freeform_template
from apps.domains.messaging.services.recipients import resolve_student_message_recipients


CONTENT_PLACEHOLDERS = ("#{공지내용}", "#{내용}", "#{선생님메모}", "#{선생님메모1}")


def _dispatch_or_schedule_message(*, tenant_id: int, trigger: str, payload: dict, scheduled_send_at):
    if scheduled_send_at:
        from apps.domains.messaging.scheduled import schedule_notification_at

        schedule_notification_at(
            tenant_id=tenant_id,
            trigger=trigger,
            send_at=scheduled_send_at,
            payload=payload,
        )
        return "scheduled"

    from apps.domains.messaging.services import enqueue_sms

    return "enqueued" if enqueue_sms(**payload) else "failed"


def _append_teacher_memo_aliases(replacements: list[dict], value: str) -> None:
    replacements.append({"key": "선생님메모", "value": value})
    replacements.append({"key": "선생님메모1", "value": value})


class SendMessageView(APIView):
    """
    POST: 선택 학생(들) 또는 직원(들)에게 메시지 발송 (SQS enqueue → 워커가 Solapi 발송).
    - student_ids + send_to "student"|"parent": 학생/학부모 전화로 발송
    - staff_ids + send_to "staff": 직원 전화로 발송
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request):
        tenant = request.tenant
        if not can_send_messages(request, tenant):
            return Response(
                {"detail": "메시지 발송 권한이 없습니다. 관리자 또는 강사 권한이 필요합니다."},
                status=status.HTTP_403_FORBIDDEN,
            )

        ser = SendMessageRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        send_to = data["send_to"]
        message_mode = "alimtalk"
        template_id = data.get("template_id")
        raw_body = (data.get("raw_body") or "").strip()
        raw_subject = (data.get("raw_subject") or "").strip()
        scheduled_send_at = data.get("scheduled_send_at")

        # 알림톡 전용 정책: 발신번호는 선택값이다.
        sender = (tenant.messaging_sender or "").strip()

        from apps.domains.messaging.services import get_tenant_site_url
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
                template_id, raw_body, raw_subject, scheduled_send_at,
            )

        # 학생/학부모 수신
        student_ids = data.get("student_ids") or []
        recipients = resolve_student_message_recipients(
            tenant,
            student_ids,
            send_to=send_to,
        )
        if not recipients:
            return Response(
                {"detail": "선택한 학생을 찾을 수 없거나 삭제된 학생입니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(recipients) > 200:
            return Response(
                {"detail": f"한 번에 최대 200명까지 발송할 수 있습니다. (선택: {len(recipients)}명)"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        body_base = (raw_body or "").strip()
        subject_base = (raw_subject or "").strip()
        t = None
        solapi_template_id = ""
        user_custom_content = ""
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
            tpl_body = t.body or ""
            if body_base and any(marker in tpl_body for marker in CONTENT_PLACEHOLDERS):
                user_custom_content = body_base
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
                # SSOT (2026-05-14, domain-policy §5): 학원장이 본문 어떻게 수정해도 봉투
                # (검수 양식)는 유지되어 발송. t.category 매핑 없거나 t=None 일 때
                # frontend가 보낸 block_category로 unified 매칭 재시도.
                # 학원장 limglish 보고 "테스트1 후 테스트2 발송 검수 에러"의 root cause:
                # frontend race / 양식 변경으로 template_id 누락 시 검수 에러 차단.
                block_category = (data.get("block_category") or "").strip()
                if block_category:
                    fb_tt, fb_sid = get_unified_for_category(block_category, tpl_name, alimtalk_extra_vars)
                    if fb_tt and fb_sid:
                        use_unified = True
                        unified_template_type = fb_tt
                        solapi_template_id = fb_sid
                if not solapi_template_id:
                    use_unified = False
                    solapi_template_id = (t.solapi_template_id or "").strip() if t else ""

        # 알림톡 직접 작성: 템플릿 미선택 또는 block_category 미매핑이어도 승인된 자유양식 봉투로 발송한다.
        if message_mode == "alimtalk" and not solapi_template_id:
            freeform = resolve_freeform_template(tenant.id)
            if freeform:
                t = freeform
                solapi_template_id = (freeform.solapi_template_id or "").strip()
                user_custom_content = body_base
                if not subject_base:
                    subject_base = (freeform.subject or "").strip()

        if message_mode == "alimtalk" and solapi_template_id and not use_unified:
            if t and getattr(t, "solapi_status", None) != "APPROVED":
                return Response(
                    {"detail": "알림톡 발송에는 검수 승인된 템플릿이 필요합니다. 템플릿 검수를 먼저 신청해 주세요."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if message_mode == "alimtalk" and not solapi_template_id:
            return Response(
                {"detail": "알림톡 발송에는 검수 승인된 템플릿이 필요합니다. 템플릿 검수를 먼저 신청해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not body_base:
            return Response(
                {"detail": "발송할 본문이 비어 있습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        enqueued = 0
        scheduled = 0
        skipped_no_phone = 0
        enqueue_failed = 0
        for recipient in recipients:
            phone = recipient.phone
            if not phone or len(phone) < 10:
                skipped_no_phone += 1
                continue
            name = recipient.student_name
            name_2 = name[-2:] if len(name) >= 2 else name
            name_3 = name
            site_url = get_tenant_site_url(request.tenant) or ""
            academy_name = (tenant.name or "").strip()

            # 학생별 개별 변수 merge
            student_extra = dict(extra_vars_per_student.get(recipient.student_id, {}))

            # SSOT (2026-05-13): 학생별 치환된 본문 우선. frontend SessionScoresEntryPage 일괄 path가
            # substituteScoreVars 결과를 _body_subst 로 보냄. backend가 그대로 사용 → 모든 score
            # sub-variable(#{시험1명}, #{시험1점수}, #{과제N...}, #{시험총점}) 치환됨. 학원장 limglish 보고
            # "본문 변수 미치환 → 빈 자리" 결함 fix.
            student_body = student_extra.pop("_body_subst", None) or body_base

            merged_context = {**alimtalk_extra_vars, **student_extra}

            # SMS용 text: 변수 치환 — merged_context 전체 key 순회 (고정 list 제거).
            text = (
                student_body.replace("#{학생이름}", name)
                .replace("#{학생이름2}", name_2)
                .replace("#{학생이름3}", name_3)
                .replace("#{학원명}", academy_name)
                .replace("#{학원이름}", academy_name)
                .replace("#{사이트링크}", site_url)
            )
            for var_key, var_val in merged_context.items():
                if var_key.startswith("_"):
                    continue  # internal hint (e.g. _body_subst) skip
                text = text.replace(f"#{{{var_key}}}", str(var_val or ""))
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
                    # SSOT (2026-05-13): student_body (학생별 치환된 본문) 사용 → 봉투의 #{선생님메모} 변수에
                    # 정확한 학생별 점수가 들어감.
                    alimtalk_replacements = build_manual_replacements(
                        template_type=unified_template_type,
                        content_body=student_body,
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
                        if var_key.startswith("_"):
                            continue  # internal hint
                        if var_val and var_key not in ("학생이름", "학생이름2", "학생이름3", "사이트링크"):
                            alimtalk_replacements.append({"key": var_key, "value": str(var_val)})
                    if user_custom_content:
                        alimtalk_replacements.append({"key": "공지내용", "value": user_custom_content})
                        alimtalk_replacements.append({"key": "내용", "value": user_custom_content})
                        _append_teacher_memo_aliases(alimtalk_replacements, user_custom_content)

            try:
                dispatch_result = _dispatch_or_schedule_message(
                    tenant_id=tenant.id,
                    trigger="manual_send",
                    scheduled_send_at=scheduled_send_at,
                    payload={
                        "tenant_id": tenant.id,
                        "to": phone,
                        "text": text,
                        "sender": sender,
                        "message_mode": message_mode,
                        "template_id": template_id_solapi,
                        "alimtalk_replacements": alimtalk_replacements,
                        "event_type": "manual_send",
                        "target_type": "student" if send_to != "parent" else "parent",
                        "target_id": recipient.student_id,
                        "target_name": name,
                    },
                )
            except MessagingPolicyError as e:
                return Response(
                    {"detail": str(e) or "문자(SMS) 발송은 내 테넌트에서만 가능합니다."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if dispatch_result == "enqueued":
                enqueued += 1
            elif dispatch_result == "scheduled":
                scheduled += 1
            else:
                enqueue_failed += 1

        accepted = enqueued + scheduled
        detail = f"{'예약됨' if scheduled_send_at else '발송 예정'} {accepted}건"
        if enqueue_failed:
            detail += f" (큐 등록 실패 {enqueue_failed}건)"
        if skipped_no_phone:
            detail += f" (전화번호 없음 {skipped_no_phone}건)"
        return Response({
            "detail": detail + ".",
            "enqueued": enqueued,
            "scheduled": scheduled,
            "enqueue_failed": enqueue_failed,
            "skipped_no_phone": skipped_no_phone,
        }, status=status.HTTP_200_OK)

    def _send_to_staff(
        self, request, tenant, data, sender, message_mode,
        template_id, raw_body, raw_subject, scheduled_send_at,
    ):
        Staff = django_apps.get_model("staffs", "Staff")
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
            if body_base and any(marker in tpl_body for marker in CONTENT_PLACEHOLDERS):
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
                {"detail": "알림톡 발송에는 검수 승인된 템플릿이 필요합니다. 템플릿 검수를 먼저 신청해 주세요."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not body_base:
            return Response(
                {"detail": "발송할 본문이 비어 있습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.domains.messaging.services import get_tenant_site_url
        from apps.domains.messaging.policy import MessagingPolicyError

        enqueued = 0
        scheduled = 0
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
                        _append_teacher_memo_aliases(alimtalk_replacements, user_custom_content)

            try:
                dispatch_result = _dispatch_or_schedule_message(
                    tenant_id=tenant.id,
                    trigger="manual_send_staff",
                    scheduled_send_at=scheduled_send_at,
                    payload={
                        "tenant_id": tenant.id,
                        "to": phone,
                        "text": text,
                        "sender": sender,
                        "message_mode": message_mode,
                        "template_id": template_id_solapi,
                        "alimtalk_replacements": alimtalk_replacements,
                        "event_type": "manual_send_staff",
                        "target_type": "staff",
                        "target_id": s.id,
                        "target_name": name,
                    },
                )
            except MessagingPolicyError as e:
                return Response(
                    {"detail": str(e) or "문자(SMS) 발송은 내 테넌트에서만 가능합니다."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            if dispatch_result == "enqueued":
                enqueued += 1
            elif dispatch_result == "scheduled":
                scheduled += 1
            else:
                enqueue_failed += 1

        accepted = enqueued + scheduled
        detail = f"{'예약됨' if scheduled_send_at else '발송 예정'} {accepted}건"
        if enqueue_failed:
            detail += f" (큐 등록 실패 {enqueue_failed}건)"
        if skipped_no_phone:
            detail += f" (전화번호 없음 {skipped_no_phone}건)"
        return Response({
            "detail": detail + ".",
            "enqueued": enqueued,
            "scheduled": scheduled,
            "enqueue_failed": enqueue_failed,
            "skipped_no_phone": skipped_no_phone,
        }, status=status.HTTP_200_OK)
