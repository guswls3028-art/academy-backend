from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db.models import Q
from django.utils import timezone

from apps.core.models import WorkerHeartbeatModel
from apps.domains.messaging.alimtalk_content_builders import (
    SYSTEM_TEMPLATE_CATEGORIES,
    get_unified_for_category,
)
from apps.domains.messaging.effective_templates import resolve_effective_template_status
from apps.domains.messaging.models import AutoSendConfig, MessageTemplate, NotificationLog, ScheduledNotification
from apps.domains.messaging.policy import get_owner_tenant_id, get_trigger_implementation_status
from apps.domains.messaging.selectors import resolve_freeform_template
from apps.domains.messaging.services.recipients import normalize_phone, resolve_student_message_recipients


MAX_MANUAL_RECIPIENTS = 200
HOURLY_SEND_LIMIT = 500
WORKER_STALE_AFTER_MINUTES = 5


@dataclass(frozen=True)
class PreflightIssue:
    code: str
    title: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "title": self.title, "detail": self.detail}


@dataclass(frozen=True)
class TemplatePlan:
    ok: bool
    source: str
    name: str = ""
    solapi_template_id: str = ""
    solapi_status: str = ""
    detail: str = ""
    uses_unified_template: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "source": self.source,
            "name": self.name,
            "solapi_template_id": self.solapi_template_id,
            "solapi_status": self.solapi_status,
            "detail": self.detail,
            "uses_unified_template": self.uses_unified_template,
        }


def _resolve_template_for_manual_send(tenant, data: dict[str, Any]) -> TemplatePlan:
    message_mode = "alimtalk"
    template_id = data.get("template_id")
    raw_body = (data.get("raw_body") or "").strip()
    raw_subject = (data.get("raw_subject") or "").strip()
    block_category = (data.get("block_category") or "").strip()
    extra_vars = data.get("alimtalk_extra_vars") or {}

    if message_mode != "alimtalk":
        return TemplatePlan(ok=False, source="unsupported", detail="현재 수동 발송은 알림톡만 지원합니다.")

    template = None
    if template_id:
        template = MessageTemplate.objects.filter(tenant=tenant, pk=template_id).first()
        if not template:
            owner_id = get_owner_tenant_id()
            if int(tenant.id) != owner_id:
                template = MessageTemplate.objects.filter(
                    tenant_id=owner_id,
                    pk=template_id,
                    solapi_status="APPROVED",
                ).first()
        if not template:
            return TemplatePlan(ok=False, source="missing", detail="선택한 템플릿을 찾을 수 없습니다.")

    body_base = raw_body or ((template.body or "").strip() if template else "")
    if not body_base:
        return TemplatePlan(ok=False, source="empty_body", detail="발송할 본문이 비어 있습니다.")

    category = (template.category if template else "") or ""
    template_name = (template.name if template else "") or ""
    unified_type, unified_sid = get_unified_for_category(category, template_name, extra_vars)
    if not unified_sid and block_category:
        unified_type, unified_sid = get_unified_for_category(block_category, template_name, extra_vars)
    if unified_type and unified_sid:
        return TemplatePlan(
            ok=True,
            source="unified",
            name=template_name or "시스템 통합 알림톡",
            solapi_template_id=unified_sid,
            solapi_status="APPROVED",
            detail="카카오 검수 완료된 시스템 봉투로 발송됩니다.",
            uses_unified_template=True,
        )

    if template:
        solapi_id = (template.solapi_template_id or "").strip()
        if category in SYSTEM_TEMPLATE_CATEGORIES:
            if solapi_id and template.solapi_status == "APPROVED":
                return TemplatePlan(
                    ok=True,
                    source="selected",
                    name=template.name,
                    solapi_template_id=solapi_id,
                    solapi_status=template.solapi_status,
                    detail="선택한 승인 템플릿으로 발송됩니다.",
                )
            return TemplatePlan(
                ok=False,
                source="selected_unapproved",
                name=template.name,
                solapi_template_id=solapi_id,
                solapi_status=template.solapi_status,
                detail="선택한 시스템 템플릿이 아직 카카오 검수 승인 상태가 아닙니다.",
            )
        if solapi_id and template.solapi_status == "APPROVED":
            return TemplatePlan(
                ok=True,
                source="selected",
                name=template.name,
                solapi_template_id=solapi_id,
                solapi_status=template.solapi_status,
                detail="선택한 승인 템플릿으로 발송됩니다.",
            )
        if solapi_id:
            return TemplatePlan(
                ok=False,
                source="selected_unapproved",
                name=template.name,
                solapi_template_id=solapi_id,
                solapi_status=template.solapi_status,
                detail="선택한 템플릿이 아직 카카오 검수 승인 상태가 아닙니다.",
            )

    freeform = resolve_freeform_template(tenant.id)
    if freeform and raw_body:
        return TemplatePlan(
            ok=True,
            source="freeform",
            name=freeform.name,
            solapi_template_id=(freeform.solapi_template_id or "").strip(),
            solapi_status=freeform.solapi_status,
            detail="승인된 자유양식 봉투에 작성한 본문을 담아 발송됩니다.",
        )

    if raw_subject:
        return TemplatePlan(ok=False, source="missing", detail="알림톡 발송에는 검수 승인된 템플릿이 필요합니다.")
    return TemplatePlan(ok=False, source="missing", detail="검수 승인된 템플릿이나 자유양식 봉투가 없습니다.")


def _phone_summary(phones: list[str]) -> dict[str, int]:
    valid = [phone for phone in phones if phone and len(phone) >= 10]
    unique = set(valid)
    return {
        "valid_phone": len(valid),
        "skipped_no_phone": len(phones) - len(valid),
        "duplicate_phone": max(0, len(valid) - len(unique)),
        "unique_phone": len(unique),
    }


def build_send_preflight(tenant, data: dict[str, Any]) -> dict[str, Any]:
    send_to = data.get("send_to") or "parent"
    scheduled_send_at = data.get("scheduled_send_at")
    blockers: list[PreflightIssue] = []
    warnings: list[PreflightIssue] = []

    selected_count = 0
    resolved_count = 0
    phones: list[str] = []

    student_ids = list(dict.fromkeys(int(student_id) for student_id in (data.get("student_ids") or [])))
    selected_count = len(student_ids)
    recipients = resolve_student_message_recipients(tenant, student_ids, send_to=send_to)
    resolved_count = len(recipients)
    phones = [recipient.phone for recipient in recipients]

    invalid_or_deleted = max(0, selected_count - resolved_count)
    phone_summary = _phone_summary(phones)
    valid_phone = phone_summary["valid_phone"]

    if selected_count == 0:
        blockers.append(PreflightIssue("no_recipient", "수신자 없음", "발송 대상을 먼저 선택해 주세요."))
    if resolved_count == 0 and selected_count:
        blockers.append(PreflightIssue("recipient_not_found", "대상 확인 필요", "선택한 대상이 없거나 삭제되었습니다."))
    if resolved_count > MAX_MANUAL_RECIPIENTS:
        blockers.append(
            PreflightIssue(
                "recipient_limit",
                "대상 초과",
                f"한 번에 최대 {MAX_MANUAL_RECIPIENTS}명까지 발송할 수 있습니다.",
            )
        )
    if valid_phone == 0 and resolved_count:
        blockers.append(PreflightIssue("no_valid_phone", "전화번호 없음", "발송 가능한 전화번호가 없습니다."))
    elif phone_summary["skipped_no_phone"] > 0:
        warnings.append(
            PreflightIssue(
                "missing_phone",
                "전화번호 누락",
                f"{phone_summary['skipped_no_phone']}건은 전화번호가 없어 제외됩니다.",
            )
        )
    if phone_summary["duplicate_phone"] > 0:
        warnings.append(
            PreflightIssue(
                "duplicate_phone",
                "중복 번호",
                f"동일 번호가 {phone_summary['duplicate_phone']}건 있습니다. 실제 발송 대상인지 확인해 주세요.",
            )
        )
    if invalid_or_deleted > 0:
        warnings.append(
            PreflightIssue(
                "invalid_or_deleted",
                "선택 대상 일부 제외",
                f"{invalid_or_deleted}건은 삭제되었거나 현재 테넌트 대상이 아닙니다.",
            )
        )

    template_plan = _resolve_template_for_manual_send(tenant, data)
    if not template_plan.ok:
        blockers.append(PreflightIssue("template_not_ready", "템플릿 검수 필요", template_plan.detail))

    one_hour_ago = timezone.now() - timedelta(hours=1)
    recent_count = NotificationLog.objects.filter(tenant=tenant, sent_at__gte=one_hour_ago).count()
    remaining_hourly = max(0, HOURLY_SEND_LIMIT - recent_count)
    expected_dispatches = valid_phone
    if expected_dispatches > remaining_hourly:
        blockers.append(
            PreflightIssue(
                "hourly_limit",
                "시간당 발송 한도 초과",
                f"최근 1시간 발송 {recent_count}건 기준으로 {remaining_hourly}건만 추가 발송할 수 있습니다.",
            )
        )
    elif remaining_hourly <= 50:
        warnings.append(
            PreflightIssue(
                "hourly_limit_near",
                "발송 한도 임박",
                f"최근 1시간 내 {recent_count}건을 발송했습니다.",
            )
        )

    if scheduled_send_at:
        if scheduled_send_at <= timezone.now():
            blockers.append(PreflightIssue("schedule_in_past", "예약 시각 확인", "예약 발송 시각은 현재 이후여야 합니다."))
    else:
        heartbeat = _latest_worker_heartbeat()
        if heartbeat["status"] in {"unknown", "stale"}:
            warnings.append(
                PreflightIssue(
                    "worker_attention",
                    "워커 상태 확인",
                    "즉시 발송은 큐 등록 후 워커가 처리합니다. 발송 내역에서 최종 결과를 확인해 주세요.",
                )
            )

    return {
        "ok": not blockers,
        "can_send": not blockers,
        "mode": "scheduled" if scheduled_send_at else "now",
        "send_to": send_to,
        "recipient": {
            "selected": selected_count,
            "resolved": resolved_count,
            "valid_phone": valid_phone,
            "skipped_no_phone": phone_summary["skipped_no_phone"],
            "duplicate_phone": phone_summary["duplicate_phone"],
            "unique_phone": phone_summary["unique_phone"],
            "invalid_or_deleted": invalid_or_deleted,
            "limit": MAX_MANUAL_RECIPIENTS,
        },
        "template": template_plan.as_dict(),
        "limits": {
            "hourly_limit": HOURLY_SEND_LIMIT,
            "sent_last_hour": recent_count,
            "remaining_this_hour": remaining_hourly,
        },
        "blockers": [issue.as_dict() for issue in blockers],
        "warnings": [issue.as_dict() for issue in warnings],
    }


def _latest_worker_heartbeat() -> dict[str, Any]:
    now = timezone.now()
    heartbeat = WorkerHeartbeatModel.objects.filter(name="messaging").order_by("-last_seen_at").first()
    if not heartbeat:
        return {
            "status": "unknown",
            "last_seen_at": None,
            "age_seconds": None,
            "instance": "",
            "version": "",
        }
    age_seconds = max(0, int((now - heartbeat.last_seen_at).total_seconds()))
    status = "stale" if age_seconds > WORKER_STALE_AFTER_MINUTES * 60 else "ok"
    return {
        "status": status,
        "last_seen_at": heartbeat.last_seen_at,
        "age_seconds": age_seconds,
        "instance": heartbeat.instance,
        "version": heartbeat.version,
    }


def build_messaging_operations_status(tenant) -> dict[str, Any]:
    now = timezone.now()
    since_24h = now - timedelta(hours=24)
    scheduled_qs = ScheduledNotification.objects.filter(tenant=tenant)
    log_qs = NotificationLog.objects.filter(tenant=tenant, sent_at__gte=since_24h)

    pending_qs = scheduled_qs.filter(status=ScheduledNotification.Status.PENDING)
    failed_scheduled_24h = scheduled_qs.filter(
        status=ScheduledNotification.Status.FAILED,
        created_at__gte=since_24h,
    ).count()
    pending = pending_qs.count()
    overdue = pending_qs.filter(send_at__lte=now - timedelta(minutes=2)).count()
    due_now = pending_qs.filter(send_at__lte=now).count()

    processing = log_qs.filter(status="processing").count()
    failed = log_qs.filter(Q(success=False) | Q(status="failed")).exclude(status="processing").count()
    sent = log_qs.filter(success=True).count()

    auto_configs = AutoSendConfig.objects.filter(tenant=tenant).select_related("template")
    enabled_configs = list(auto_configs.filter(enabled=True))
    enabled_without_template = 0
    enabled_unapproved_template = 0
    enabled_manual_only = 0
    for config in enabled_configs:
        if get_trigger_implementation_status(config.trigger) != "implemented":
            enabled_manual_only += 1
        effective_template = resolve_effective_template_status(config)
        if not effective_template.solapi_template_id:
            enabled_without_template += 1
            continue
        if not effective_template.is_approved:
            enabled_unapproved_template += 1

    freeform = resolve_freeform_template(tenant.id)
    approved_count = MessageTemplate.objects.filter(tenant=tenant, solapi_status="APPROVED").count()
    owner_id = get_owner_tenant_id()
    owner_approved_count = 0
    if int(tenant.id) != owner_id:
        owner_approved_count = MessageTemplate.objects.filter(tenant_id=owner_id, solapi_status="APPROVED").count()

    risks: list[dict[str, str]] = []
    if overdue:
        risks.append({"code": "scheduled_overdue", "title": "예약 지연", "detail": f"예약 {overdue}건이 예정 시각을 지났습니다."})
    if failed or failed_scheduled_24h:
        risks.append({"code": "recent_failures", "title": "최근 실패", "detail": f"최근 24시간 실패 로그 {failed}건, 예약 실패 {failed_scheduled_24h}건입니다."})
    heartbeat = _latest_worker_heartbeat()
    if heartbeat["status"] != "ok":
        risks.append({"code": "worker_attention", "title": "워커 확인", "detail": "메시징 워커 heartbeat가 없거나 오래되었습니다."})
    if not freeform:
        risks.append({"code": "freeform_missing", "title": "자유양식 없음", "detail": "직접 작성 알림톡에 사용할 승인 자유양식이 없습니다."})
    if enabled_without_template or enabled_unapproved_template or enabled_manual_only:
        risks.append(
            {
                "code": "auto_send_template_attention",
                "title": "자동발송 설정 확인",
                "detail": "켜진 자동발송 중 템플릿/구현 상태 확인이 필요한 항목이 있습니다.",
            }
        )

    return {
        "checked_at": now,
        "worker": heartbeat,
        "scheduled": {
            "pending": pending,
            "due_now": due_now,
            "overdue": overdue,
            "failed_24h": failed_scheduled_24h,
        },
        "log_24h": {
            "sent": sent,
            "failed": failed,
            "processing": processing,
            "total": log_qs.count(),
        },
        "templates": {
            "approved": approved_count,
            "owner_approved": owner_approved_count,
            "freeform_available": bool(freeform),
            "freeform_template_name": freeform.name if freeform else "",
        },
        "auto_send": {
            "enabled": len(enabled_configs),
            "enabled_without_template": enabled_without_template,
            "enabled_unapproved_template": enabled_unapproved_template,
            "enabled_manual_only": enabled_manual_only,
        },
        "risks": risks,
    }
