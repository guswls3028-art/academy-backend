# apps/support/messaging/notification_dispatch.py
"""
수동 알림 발송 서비스 — preview → confirm 2단계 발송.

자동 트리거 발송이 아닌 선생의 명시적 수동 발송만 지원.
저장과 발송을 완전 분리.
"""

import logging
import uuid
from datetime import timedelta
from typing import Optional

from django.utils import timezone

logger = logging.getLogger(__name__)

PREVIEW_TOKEN_TTL_SECONDS = 300  # 5분


def build_attendance_preview(
    tenant,
    session_id: int,
    notification_type: str,
    send_to: str = "parent",
) -> dict:
    """
    출결 알림 미리보기 데이터 생성.

    Args:
        tenant: Tenant 인스턴스
        session_id: Session ID
        notification_type: "check_in" | "absent"
        send_to: "parent" | "student"

    Returns:
        {
            "recipients": [{"student_id", "student_name", "phone", "status", "message_body", "excluded", "exclude_reason"}],
            "total_count": int,
            "excluded_count": int,
            "message_template_body": str,
            "notification_type": str,
        }
    """
    from apps.domains.attendance.models import Attendance
    from apps.domains.lectures.models import Session
    from apps.support.messaging.selectors import get_auto_send_config
    from apps.support.messaging.policy import get_owner_tenant_id
    from apps.support.messaging.services import get_tenant_site_url

    session = Session.objects.select_related("lecture").filter(
        id=session_id, lecture__tenant_id=tenant.id,
    ).first()
    if not session:
        return {"error": "세션을 찾을 수 없습니다.", "recipients": [], "total_count": 0, "excluded_count": 0}

    # 트리거 매핑
    trigger_map = {
        "check_in": "check_in_complete",
        "absent": "absent_occurred",
    }
    trigger = trigger_map.get(notification_type)
    if not trigger:
        return {"error": f"지원하지 않는 알림 유형: {notification_type}", "recipients": [], "total_count": 0, "excluded_count": 0}

    # 템플릿 resolve (테넌트 → 오너 fallback)
    config = get_auto_send_config(tenant.id, trigger)
    if not config:
        owner_id = get_owner_tenant_id()
        if int(tenant.id) != owner_id:
            config = get_auto_send_config(owner_id, trigger)

    template = config.template if config else None
    effective_mode = (config.message_mode if config else "alimtalk") or "alimtalk"
    solapi_template_id = (template.solapi_template_id or "").strip() if template else ""
    solapi_approved = solapi_template_id and template.solapi_status == "APPROVED" if template else False

    # SMS-only 모드: 템플릿 본문만 있으면 됨 (APPROVED 불필요)
    # 알림톡 포함 모드 (alimtalk/both): APPROVED 템플릿 필요
    if not template or not (template.body or "").strip():
        return {"error": "발송 템플릿이 없습니다.", "recipients": [], "total_count": 0, "excluded_count": 0}
    if effective_mode in ("alimtalk", "both") and not solapi_approved:
        # 알림톡 불가 시: SMS로 폴백 가능한지 확인
        from apps.support.messaging.policy import can_send_sms
        if effective_mode == "both" and can_send_sms(tenant.id):
            effective_mode = "sms"  # 알림톡 불가, SMS만 발송
            solapi_template_id = ""
        elif effective_mode == "alimtalk":
            return {"error": "승인된 알림톡 템플릿이 없습니다. SMS 모드로 변경하거나 템플릿 검수를 요청하세요.", "recipients": [], "total_count": 0, "excluded_count": 0}
        else:
            return {"error": "승인된 알림톡 템플릿이 없고 SMS 발송도 불가합니다.", "recipients": [], "total_count": 0, "excluded_count": 0}

    # 출결 상태별 필터
    if notification_type == "check_in":
        status_filter = ["PRESENT", "LATE", "ONLINE", "SUPPLEMENT"]
    else:  # absent
        status_filter = ["ABSENT"]

    attendances = (
        Attendance.objects
        .filter(
            session_id=session_id,
            session__lecture__tenant_id=tenant.id,
            status__in=status_filter,
        )
        .exclude(enrollment__status="INACTIVE")
        .select_related("enrollment__student", "session__lecture")
    )

    academy_name = (tenant.name or "").strip()
    site_url = get_tenant_site_url(tenant) or ""
    now = timezone.localtime()

    recipients = []
    for att in attendances:
        student = att.enrollment.student
        name = (student.name or "").strip()
        name_2 = name[1:] if len(name) >= 2 else name  # 성(첫 글자) 제외 = 이름만

        if send_to == "parent":
            phone = (student.parent_phone or "").replace("-", "").strip()
        else:
            phone = (student.phone or "").replace("-", "").strip()

        excluded = False
        exclude_reason = ""
        if not phone or len(phone) < 10:
            excluded = True
            exclude_reason = "전화번호 없음" if not phone else "전화번호 형식 오류"

        # 본문 치환
        body = (template.body or "").strip()
        # 반 정보 (section_mode일 때)
        section = getattr(att.session, "section", None)
        section_label = ""
        if section:
            prefix = "클리닉 " if section.section_type == "CLINIC" else ""
            section_label = f"{prefix}{section.label}반"

        context = {
            "학원명": academy_name,
            "학생이름": name,
            "학생이름2": name_2,
            "사이트링크": site_url,
            "강의명": att.session.lecture.title or "",
            "차시명": att.session.title or f"{att.session.order}차시",
            "반이름": section_label,
            "날짜": str(att.session.date) if att.session.date else now.strftime("%Y-%m-%d"),
            "시간": now.strftime("%H:%M"),
        }
        for k, v in context.items():
            body = body.replace(f"#{{{k}}}", v)

        recipients.append({
            "student_id": student.id,
            "student_name": name,
            "phone": phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else phone,
            "phone_raw": phone,
            "status": att.status,
            "message_body": body,
            "excluded": excluded,
            "exclude_reason": exclude_reason,
            "alimtalk_replacements": [{"key": k, "value": v} for k, v in context.items()],
        })

    sendable = [r for r in recipients if not r["excluded"]]

    return {
        "recipients": recipients,
        "total_count": len(sendable),
        "excluded_count": len(recipients) - len(sendable),
        "message_template_body": (template.body or "").strip(),
        "notification_type": notification_type,
        "session_title": session.title or f"{session.order}차시",
        "lecture_title": session.lecture.title or "",
        "solapi_template_id": solapi_template_id if solapi_approved else "",
        "message_mode": effective_mode,
    }


def build_student_list_preview(
    tenant,
    trigger: str,
    student_ids: list[int],
    send_to: str = "parent",
    context_per_student: dict = None,
    shared_context: dict = None,
) -> dict:
    """
    학생 ID 목록 기반 범용 알림 미리보기.
    시험 성적 공개, 퇴원 안내, 과제 미제출 등 모든 MANUAL_DEFAULT에 사용.
    """
    from apps.domains.students.models import Student
    from apps.support.messaging.selectors import get_auto_send_config
    from apps.support.messaging.policy import get_owner_tenant_id
    from apps.support.messaging.services import get_tenant_site_url

    config = get_auto_send_config(tenant.id, trigger)
    if not config:
        owner_id = get_owner_tenant_id()
        if int(tenant.id) != owner_id:
            config = get_auto_send_config(owner_id, trigger)

    template = config.template if config else None
    effective_mode = (config.message_mode if config else "alimtalk") or "alimtalk"
    solapi_template_id = (template.solapi_template_id or "").strip() if template else ""
    solapi_approved = solapi_template_id and template.solapi_status == "APPROVED" if template else False

    if not template or not (template.body or "").strip():
        return {"error": "발송 템플릿이 없습니다.", "recipients": [], "total_count": 0, "excluded_count": 0}
    if effective_mode in ("alimtalk", "both") and not solapi_approved:
        from apps.support.messaging.policy import can_send_sms
        if effective_mode == "both" and can_send_sms(tenant.id):
            effective_mode = "sms"
            solapi_template_id = ""
        elif effective_mode == "alimtalk":
            return {"error": "승인된 알림톡 템플릿이 없습니다. SMS 모드로 변경하거나 템플릿 검수를 요청하세요.", "recipients": [], "total_count": 0, "excluded_count": 0}
        else:
            return {"error": "승인된 알림톡 템플릿이 없고 SMS 발송도 불가합니다.", "recipients": [], "total_count": 0, "excluded_count": 0}

    students = Student.objects.filter(
        id__in=student_ids, tenant_id=tenant.id, deleted_at__isnull=True,
    )

    academy_name = (tenant.name or "").strip()
    site_url = get_tenant_site_url(tenant) or ""
    context_per_student = context_per_student or {}
    shared_context = shared_context or {}

    recipients = []
    for student in students:
        name = (student.name or "").strip()
        name_2 = name[1:] if len(name) >= 2 else name  # 성(첫 글자) 제외 = 이름만

        if send_to == "parent":
            phone = (student.parent_phone or "").replace("-", "").strip()
        else:
            phone = (student.phone or "").replace("-", "").strip()

        excluded = False
        exclude_reason = ""
        if not phone or len(phone) < 10:
            excluded = True
            exclude_reason = "전화번호 없음" if not phone else "전화번호 형식 오류"

        ctx = {
            "학원명": academy_name,
            "학생이름": name,
            "학생이름2": name_2,
            "사이트링크": site_url,
            **shared_context,
            **context_per_student.get(student.id, {}),
        }

        body = (template.body or "").strip()
        for k, v in ctx.items():
            body = body.replace(f"#{{{k}}}", str(v))

        recipients.append({
            "student_id": student.id,
            "student_name": name,
            "phone": phone[:3] + "****" + phone[-4:] if len(phone) >= 7 else phone,
            "phone_raw": phone,
            "message_body": body,
            "excluded": excluded,
            "exclude_reason": exclude_reason,
            "alimtalk_replacements": [{"key": k, "value": str(v)} for k, v in ctx.items()],
        })

    sendable = [r for r in recipients if not r["excluded"]]
    return {
        "recipients": recipients,
        "total_count": len(sendable),
        "excluded_count": len(recipients) - len(sendable),
        "message_template_body": (template.body or "").strip(),
        "notification_type": trigger,
        "solapi_template_id": solapi_template_id if solapi_approved else "",
        "message_mode": effective_mode,
    }


def create_preview_token(
    tenant,
    preview_data: dict,
    staff_id: Optional[int],
    session_type: str,
    session_id: int,
    notification_type: str,
    send_to: str,
) -> str:
    """preview 데이터를 토큰에 저장하고 UUID 반환."""
    from apps.support.messaging.models import NotificationPreviewToken

    token_uuid = uuid.uuid4()
    expires = timezone.now() + timedelta(seconds=PREVIEW_TOKEN_TTL_SECONDS)

    # 발송 가능한 수신자만 payload에 저장
    sendable = [r for r in preview_data.get("recipients", []) if not r.get("excluded")]
    payload = {
        "recipients": sendable,
        "solapi_template_id": preview_data.get("solapi_template_id", ""),
        "message_mode": preview_data.get("message_mode", "alimtalk"),
        "message_template_body": preview_data.get("message_template_body", ""),
        "notification_type": notification_type,
        "send_to": send_to,
    }

    NotificationPreviewToken.objects.create(
        token=token_uuid,
        tenant=tenant,
        notification_type=notification_type,
        session_type=session_type,
        session_id=session_id,
        send_to=send_to,
        payload=payload,
        created_by_id=staff_id,
        expires_at=expires,
    )

    return str(token_uuid)


def consume_preview_token(token_str: str, tenant) -> dict:
    """
    토큰을 소비하고 payload 반환.
    실패 시 {"error": "..."} 반환.
    """
    from apps.support.messaging.models import NotificationPreviewToken

    try:
        token_uuid = uuid.UUID(token_str)
    except (ValueError, AttributeError):
        return {"error": "유효하지 않은 토큰 형식입니다."}

    token = NotificationPreviewToken.objects.filter(
        token=token_uuid,
        tenant=tenant,
    ).first()

    if not token:
        return {"error": "토큰을 찾을 수 없습니다."}

    if token.used_at is not None:
        return {"error": "이미 사용된 토큰입니다. 중복 발송이 방지되었습니다."}

    if timezone.now() > token.expires_at:
        return {"error": "토큰이 만료되었습니다. 미리보기를 다시 실행해주세요."}

    # Atomic consume (race condition 방지)
    from apps.support.messaging.models import NotificationPreviewToken as NPT
    now = timezone.now()
    batch_id = uuid.uuid4()
    updated = NPT.objects.filter(
        token=token_uuid,
        used_at__isnull=True,
    ).update(used_at=now, batch_id=batch_id)

    if updated == 0:
        return {"error": "이미 사용된 토큰입니다. 중복 발송이 방지되었습니다."}

    token.refresh_from_db()
    return {
        "payload": token.payload,
        "batch_id": str(batch_id),
        "staff_id": token.created_by_id,
        "notification_type": token.notification_type,
    }


def execute_notification_batch(
    tenant,
    payload: dict,
    batch_id: str,
    staff_id: Optional[int],
) -> dict:
    """
    payload의 수신자 목록에 대해 알림톡 발송 실행.
    """
    from apps.support.messaging.services import enqueue_sms
    from apps.support.messaging.policy import check_recipient_allowed, MessagingPolicyError, can_send_sms

    recipients = payload.get("recipients", [])
    solapi_template_id = payload.get("solapi_template_id", "")
    raw_message_mode = payload.get("message_mode", "alimtalk")
    notification_type = payload.get("notification_type", "")

    # 채널 가용성 사전 확인 후 modes_to_send 결정
    _can_sms = can_send_sms(tenant.id)
    _can_alimtalk = bool(solapi_template_id)
    if raw_message_mode == "both":
        modes_to_send = []
        if _can_alimtalk:
            modes_to_send.append("alimtalk")
        if _can_sms:
            modes_to_send.append("sms")
    elif raw_message_mode == "sms":
        if _can_sms:
            modes_to_send = ["sms"]
        elif _can_alimtalk:
            modes_to_send = ["alimtalk"]  # SMS 불가 → 알림톡 폴백
            logger.info("batch: SMS unavailable for tenant=%s, fallback to alimtalk", tenant.id)
        else:
            modes_to_send = []
    else:
        modes_to_send = ["alimtalk"] if _can_alimtalk else []

    if not modes_to_send:
        logger.warning(
            "batch %s: no available channel (mode=%s, can_sms=%s, can_alimtalk=%s)",
            batch_id, raw_message_mode, _can_sms, _can_alimtalk,
        )
        return {
            "batch_id": batch_id,
            "sent_count": 0,
            "failed_count": len([r for r in recipients if not r.get("excluded")]),
            "blocked_count": 0,
            "error": "발송 가능한 채널이 없습니다.",
        }

    sent = 0
    failed = 0
    blocked = 0

    for r in recipients:
        phone = r.get("phone_raw", "")
        if not phone or r.get("excluded"):
            continue

        if not check_recipient_allowed(phone):
            blocked += 1
            logger.info("batch %s: blocked recipient %s (whitelist)", batch_id, phone[:4] + "****")
            continue

        for mode in modes_to_send:
            try:
                ok = enqueue_sms(
                    tenant_id=tenant.id,
                    to=phone,
                    text=r.get("message_body", ""),
                    message_mode=mode,
                    template_id=solapi_template_id if mode == "alimtalk" else None,
                    alimtalk_replacements=r.get("alimtalk_replacements", []) if mode == "alimtalk" else None,
                    event_type=f"manual_{notification_type}",
                    target_type="student",
                    target_id=r.get("student_id"),
                    occurrence_key=f"batch_{batch_id}",
                )
                if ok:
                    sent += 1
                else:
                    failed += 1
            except MessagingPolicyError:
                failed += 1
            except Exception:
                logger.exception("batch %s: enqueue failed for %s mode=%s", batch_id, phone[:4] + "****", mode)
                failed += 1

    logger.info(
        "notification batch completed: batch_id=%s type=%s sent=%d failed=%d blocked=%d staff=%s",
        batch_id, notification_type, sent, failed, blocked, staff_id,
    )

    return {
        "batch_id": batch_id,
        "sent_count": sent,
        "failed_count": failed,
        "blocked_count": blocked,
    }
