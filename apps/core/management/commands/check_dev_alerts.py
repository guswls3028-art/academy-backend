# PATH: apps/core/management/commands/check_dev_alerts.py
"""
/dev 운영 알림 룰.

화이트리스트 룰을 평가해서 임계치 초과 시 Slack incoming webhook으로 전송한다.
사용자가 실제로 겪은 오류는 고정 통제번호로 운영자 SMS도 전송한다.
크론에서 호출: python manage.py check_dev_alerts [--dry-run] [--silent]

Webhook 설정:
  DEV_ALERTS_WEBHOOK_URL=https://hooks.slack.com/services/...
  비어 있으면 전송 생략 (조건 평가 + stdout만).
"""
from __future__ import annotations

import json
import hashlib
import logging
import re
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Max, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)

CONTROLLED_OPS_PHONE = "01031217466"
USER_INCIDENT_ACTIONS = (
    "user_incident.manual",
    "user_incident.frontend_exception",
    "user_incident.backend_5xx",
)
SMS_DELIVERY_ACTION = "alerts.user_incident_sms"
SMS_RATE_LIMIT_ACTION = "alerts.user_incident_sms_rate_limited"
SMS_DELIVERY_WAIT_SECONDS = 120
SMS_RETRY_COOLDOWN_MINUTES = 5
SMS_MAX_ATTEMPTS_PER_HOUR = 12
SMS_MAX_RECONCILIATIONS_PER_RUN = 10
INCIDENT_RETENTION_DAYS = 2
EXTERNAL_SIGNAL_CHOICES = ("api_user_impact",)
SMS_MAX_BYTES = 90
SMS_TENANT_LABEL_MAX_BYTES = 24


class Rule:
    def __init__(self, key: str, label: str, evaluate, severity: str = "warning"):
        self.key = key
        self.label = label
        self.evaluate = evaluate  # callable() -> dict | None
        self.severity = severity


def _exempt_ids() -> list[int]:
    ids = list(getattr(settings, "BILLING_EXEMPT_TENANT_IDS", []) or [])
    owner_id = getattr(settings, "OWNER_TENANT_ID", None)
    if owner_id is not None and owner_id not in ids:
        ids.append(owner_id)
    return ids


# ── 룰 평가자 ──

def rule_expiring_3d():
    from apps.core.models import Program
    today = timezone.localdate()
    qs = (
        Program.objects.exclude(tenant_id__in=_exempt_ids())
        .filter(
            subscription_status="active",
            subscription_expires_at__gte=today,
            subscription_expires_at__lte=today + timedelta(days=3),
        )
        .select_related("tenant")
        .order_by("subscription_expires_at")
    )
    rows = [
        {
            "tenant": p.tenant.code,
            "name": p.tenant.name,
            "expires_at": str(p.subscription_expires_at),
            "days_remaining": p.days_remaining,
        }
        for p in qs[:50]
    ]
    if not rows:
        return None
    return {
        "title": f"⏰ 만료 3일 이내 — {len(rows)}건",
        "rows": rows,
        "total": len(rows),
    }


def rule_overdue_invoices():
    from apps.billing.models import Invoice
    qs = (
        Invoice.objects.filter(status__in=["OVERDUE", "FAILED"])
        .exclude(tenant_id__in=_exempt_ids())
        .select_related("tenant")
        .order_by("-due_date")
    )
    rows = [
        {
            "tenant": inv.tenant.code if inv.tenant else "—",
            "invoice": inv.invoice_number,
            "amount": int(inv.total_amount or 0),
            "due_date": str(inv.due_date) if inv.due_date else "—",
            "status": inv.status,
        }
        for inv in qs[:50]
    ]
    if not rows:
        return None
    total_amount = qs.aggregate(t=Sum("total_amount"))["t"] or 0
    return {
        "title": f"💸 연체/실패 인보이스 — {len(rows)}건 / {int(total_amount):,}원",
        "rows": rows,
        "total": len(rows),
    }


def rule_stale_processing_payments(min_age_minutes: int | None = None):
    """Provider outcome unknown transactions require manual reconciliation."""
    from django.db.models import Q

    from apps.billing.models import PaymentTransaction

    threshold = int(
        min_age_minutes
        if min_age_minutes is not None
        else getattr(settings, "BILLING_PROCESSING_ALERT_MINUTES", 15)
    )
    cutoff = timezone.now() - timedelta(minutes=threshold)
    qs = (
        PaymentTransaction.objects.filter(status="PROCESSING")
        .exclude(tenant_id__in=_exempt_ids())
        .filter(
            Q(processing_started_at__lte=cutoff)
            | Q(processing_started_at__isnull=True, created_at__lte=cutoff)
        )
        .select_related("tenant", "invoice")
        .order_by("processing_started_at", "id")
    )
    total = qs.count()
    rows = [
        {
            "tenant": tx.tenant.code,
            "transaction_id": tx.id,
            "invoice": tx.invoice.invoice_number,
            "started_at": (
                tx.processing_started_at or tx.created_at
            ).isoformat(timespec="seconds"),
        }
        for tx in qs[:50]
    ]
    if not rows:
        return None
    return {
        "title": (
            f"🚨 결제 공급사 결과 확인 필요 — {total}건 "
            f"({threshold}분+ PROCESSING)"
        ),
        "rows": rows,
        "total": total,
    }


def rule_card_reconciliation_required(max_age_hours: int = 24):
    """Alert immediately when provider card state and local state may diverge."""
    from apps.core.models import OpsAuditLog

    since = timezone.now() - timedelta(hours=max_age_hours)
    qs = (
        OpsAuditLog.objects.filter(
            action="billing.card_reconciliation_required",
            created_at__gte=since,
        )
        .select_related("target_tenant")
        .order_by("-created_at")
    )
    total = qs.count()
    rows = [
        {
            "tenant": log.target_tenant.code if log.target_tenant else "—",
            "operation": (log.payload or {}).get("operation", "unknown"),
            "billing_key_id": (log.payload or {}).get("billing_key_id"),
            "at": log.created_at.isoformat(timespec="seconds"),
        }
        for log in qs[:50]
    ]
    if not rows:
        return None
    return {
        "title": f"🚨 카드 공급사 상태 대사 필요 — {total}건",
        "rows": rows,
        "total": total,
    }


def rule_partial_refund_reconciliation_required(max_age_hours: int = 24):
    from apps.core.models import OpsAuditLog

    since = timezone.now() - timedelta(hours=max_age_hours)
    qs = (
        OpsAuditLog.objects.filter(
            action="billing.partial_refund_reconciliation_required",
            created_at__gte=since,
        )
        .select_related("target_tenant")
        .order_by("-created_at")
    )
    total = qs.count()
    rows = [
        {
            "tenant": log.target_tenant.code if log.target_tenant else "—",
            "transaction_id": (log.payload or {}).get("transaction_id"),
            "invoice_id": (log.payload or {}).get("invoice_id"),
            "refunded_amount": (log.payload or {}).get("refunded_amount"),
            "at": log.created_at.isoformat(timespec="seconds"),
        }
        for log in qs[:50]
    ]
    if not rows:
        return None
    return {
        "title": f"🚨 부분환불 회계·구독 대사 필요 — {total}건",
        "rows": rows,
        "total": total,
    }


def rule_audit_failed_24h(threshold: int = 5):
    from apps.core.models import OpsAuditLog
    since = timezone.now() - timedelta(hours=24)
    qs = OpsAuditLog.objects.filter(created_at__gte=since, result="failed").order_by("-created_at")
    count = qs.count()
    if count < threshold:
        return None
    rows = [
        {
            "action": r.action,
            "actor": r.actor_username or "—",
            "summary": r.summary,
            "error": r.error,
            "at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in qs[:20]
    ]
    return {
        "title": f"🔥 24h 실패 작업 {count}건 (임계치 {threshold})",
        "rows": rows,
        "total": count,
    }


def rule_unanswered_inbox(min_age_hours: int = 24):
    """생성된 지 N시간 이상 지난 비공개 지원 티켓과 플랫폼 도입 문의."""
    try:
        from apps.core.models import LandingConsultRequest
        from apps.core.services.platform_inbox import PROMO_LEAD_SOURCES
        from apps.domains.community.models import (
            PostEntity,
            platform_support_q,
        )
    except Exception:
        return None
    from django.db.models import F, Max, Q

    since = timezone.now() - timedelta(hours=min_age_hours)
    support_tickets = (
        PostEntity.objects.filter(post_type="board", created_at__lte=since)
        .filter(platform_support_q())
        .annotate(
            _latest_platform_reply=Max(
                "replies__created_at",
                filter=Q(replies__author_role="platform_staff"),
            ),
            _latest_requester_reply=Max(
                "replies__created_at",
                filter=~Q(replies__author_role="platform_staff"),
            ),
        )
        .filter(
            Q(_latest_platform_reply__isnull=True)
            | Q(_latest_requester_reply__gt=F("_latest_platform_reply"))
        )
        .select_related("tenant")
        .order_by("-created_at")
    )
    support_total = support_tickets.count()
    rows = [
        {
            "tenant": p.tenant.code if p.tenant else "—",
            "source": "support",
            "title": (p.title or "")[:60],
            "at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in support_tickets[:15]
    ]
    owner_tenant_id = getattr(settings, "OWNER_TENANT_ID", None)
    lead_total = 0
    if owner_tenant_id is not None:
        leads = (
            LandingConsultRequest.objects.filter(
                tenant_id=owner_tenant_id,
                source__in=PROMO_LEAD_SOURCES,
                resolved_at__isnull=True,
                created_at__lte=since,
            )
            .select_related("tenant")
            .order_by("-created_at")
        )
        lead_total = leads.count()
        rows.extend(
            {
                "tenant": lead.tenant.code if lead.tenant else "—",
                "source": lead.source,
                "title": (lead.interest or "도입 문의")[:60],
                "at": lead.created_at.isoformat() if lead.created_at else None,
            }
            for lead in leads[:15]
        )
    if not rows:
        return None
    return {
        "title": f"📬 24h+ 미답변 문의 {support_total + lead_total}건",
        "rows": rows,
        "total": support_total + lead_total,
    }


def _incident_fingerprint(*parts: object) -> str:
    raw = json.dumps(parts, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _delivered_incident_fingerprints() -> set[str]:
    from apps.core.models import OpsAuditLog

    since = timezone.now() - timedelta(days=INCIDENT_RETENTION_DAYS)
    delivered: set[str] = set()
    payloads = OpsAuditLog.objects.filter(
        action=SMS_DELIVERY_ACTION,
        created_at__gte=since,
    ).values_list("result", "payload")
    for result, payload in payloads:
        attempt_state = str((payload or {}).get("attempt_state") or "")
        if result != "success" and attempt_state not in {
            "",
            "created",
            "registered",
            "ambiguous",
        }:
            continue
        for fingerprint in (payload or {}).get("fingerprints", []):
            if isinstance(fingerprint, str):
                delivered.add(fingerprint)
    return delivered


def _incident_scan_since():
    """늦게 확정 실패한 attempt도 재평가하도록 보존 범위 전체를 읽는다."""
    return timezone.now() - timedelta(days=INCIDENT_RETENTION_DAYS)


def rule_user_incidents(window_minutes: int | None = None):
    """최근 사용자 오류와 명시적 버그 제보를 PII 없이 묶고, 발송 완료 건은 제외한다."""
    from apps.core.models import OpsAuditLog

    since = (
        timezone.now() - timedelta(minutes=window_minutes)
        if window_minutes is not None
        else _incident_scan_since()
    )
    grouped: dict[str, dict] = {}

    incident_logs = (
        OpsAuditLog.objects.filter(
            action__in=USER_INCIDENT_ACTIONS,
            created_at__gte=since,
        )
        .select_related("target_tenant")
        .order_by("created_at", "id")
    )
    for log in incident_logs.iterator(chunk_size=500):
        payload = log.payload or {}
        tenant = log.target_tenant.code if log.target_tenant else "public"
        route = str(payload.get("route") or "/unknown")[:120]
        if log.action == "user_incident.manual":
            source = "report"
            fingerprint = _incident_fingerprint("manual", log.id)
        else:
            source = (
                "frontend"
                if log.action == "user_incident.frontend_exception"
                else "backend"
            )
            error_name = str(payload.get("error_name") or payload.get("exception_name") or "")
            bucket = int(log.created_at.timestamp() // (15 * 60))
            fingerprint = _incident_fingerprint(
                source,
                log.target_tenant_id,
                payload.get("method") or "",
                route,
                error_name[:100],
                bucket,
            )
        row = grouped.setdefault(
            fingerprint,
            {
                "fingerprint": fingerprint,
                "source": source,
                "tenant_id": log.target_tenant_id,
                "tenant": tenant,
                "route": route,
                "status": payload.get("status"),
                "count": 0,
                "at": log.created_at.isoformat(timespec="seconds"),
            },
        )
        row["count"] += 1
        row["at"] = log.created_at.isoformat(timespec="seconds")

    from apps.domains.community.models import (
        PostEntity,
        platform_support_q,
        support_kind_for_post,
    )

    bug_posts = (
        PostEntity.objects.filter(
            post_type="board",
            status="published",
            created_at__gte=since,
        )
        .filter(platform_support_q())
        .select_related("tenant")
        .order_by("created_at", "id")
    )
    for post in bug_posts.iterator(chunk_size=200):
        if support_kind_for_post(post) != "bug":
            continue
        fingerprint = _incident_fingerprint("bug_post", post.id)
        grouped[fingerprint] = {
            "fingerprint": fingerprint,
            "source": "bug_post",
            "tenant_id": post.tenant_id,
            "tenant": post.tenant.code if post.tenant else "public",
            "route": "/developer/bug",
            "status": None,
            "count": 1,
            "at": post.created_at.isoformat(timespec="seconds"),
        }

    delivered = _delivered_incident_fingerprints()
    rows = [
        row
        for fingerprint, row in grouped.items()
        if fingerprint not in delivered
    ]
    rows.sort(key=lambda row: row["at"])
    if not rows:
        return None
    total = sum(int(row["count"]) for row in rows)
    return {
        "title": f"사용자 오류/문제 신고 - {total}건",
        "rows": [
            {key: value for key, value in row.items() if key != "fingerprint"}
            for row in rows
        ],
        "fingerprints": [row["fingerprint"] for row in rows],
        "total": total,
    }


def rule_stale_workers(min_age_minutes: int = 5):
    """N분+ heartbeat 미갱신 워커. SQS 워커 process 멈춤 즉시 감지."""
    try:
        from apps.core.models import WorkerHeartbeatModel
        from apps.shared.utils.heartbeat import HEARTBEAT_RETENTION_HOURS
    except Exception:
        return None
    now = timezone.now()
    cutoff = now - timedelta(minutes=min_age_minutes)
    alert_floor = now - timedelta(hours=HEARTBEAT_RETENTION_HOURS)
    stale_workers = (
        WorkerHeartbeatModel.objects.values("name")
        .annotate(last_seen=Max("last_seen_at"))
        .filter(last_seen__lt=cutoff, last_seen__gte=alert_floor)
        .order_by("name")
    )
    total = stale_workers.count()
    rows = []
    for worker in stale_workers[:30]:
        h = (
            WorkerHeartbeatModel.objects.filter(name=worker["name"], last_seen_at=worker["last_seen"])
            .order_by("instance")
            .first()
        )
        rows.append({
            "worker": worker["name"],
            "instance": h.instance if h else "—",
            "last_seen": worker["last_seen"].isoformat(timespec="seconds") if worker["last_seen"] else None,
            "version": (h.version if h else "") or "—",
        })
    if not rows:
        return None
    return {
        "title": f"💔 워커 heartbeat 정지 {total}건 ({min_age_minutes}분+ 미갱신)",
        "rows": rows,
        "total": total,
    }


def rule_circuit_breaker_open():
    """현재 open 상태인 외부 API circuit (in-memory state는 alert에서 안 잡힘 → ops_audit 기반)."""
    try:
        from apps.core.models import OpsAuditLog
    except Exception:
        return None
    # 최근 30분 내 circuit_open 액션 (해소되지 않은 상태)
    since = timezone.now() - timedelta(minutes=30)
    qs = OpsAuditLog.objects.filter(action="circuit.open", created_at__gte=since).order_by("-created_at")
    seen_keys: set[str] = set()
    rows: list[dict] = []
    for log in qs[:50]:
        # summary 형식: "{name} (failures={n})" — name 단위로 첫 등장만 표시
        name = (log.summary or "").split(" ")[0] or "unknown"
        if name in seen_keys:
            continue
        seen_keys.add(name)
        rows.append({
            "circuit": name,
            "at": log.created_at.isoformat(timespec="seconds") if log.created_at else None,
            "summary": (log.summary or "")[:80],
        })
    if not rows:
        return None
    return {
        "title": f"⚡ 외부 API circuit open {len(rows)}개",
        "rows": rows,
        "total": len(rows),
    }


RULES: list[Rule] = [
    Rule("user_incidents", "사용자 오류/문제 신고", rule_user_incidents, "danger"),
    Rule("expiring_3d", "만료 3일 이내", rule_expiring_3d, "warning"),
    Rule("overdue_invoices", "연체/실패 인보이스", rule_overdue_invoices, "danger"),
    Rule(
        "stale_processing_payments",
        "장기 PROCESSING 결제",
        rule_stale_processing_payments,
        "danger",
    ),
    Rule(
        "card_reconciliation_required",
        "카드 공급사 상태 대사 필요",
        rule_card_reconciliation_required,
        "danger",
    ),
    Rule(
        "partial_refund_reconciliation_required",
        "부분환불 회계·구독 대사 필요",
        rule_partial_refund_reconciliation_required,
        "danger",
    ),
    Rule("audit_failed_24h", "24h 실패 작업 임계 초과", rule_audit_failed_24h, "danger"),
    Rule("unanswered_inbox", "24h+ 미답변 문의", rule_unanswered_inbox, "warning"),
    Rule("stale_workers", "워커 heartbeat 정지", rule_stale_workers, "danger"),
    Rule("circuit_open", "외부 API circuit open", rule_circuit_breaker_open, "danger"),
]


# ── Slack 전송 ──

def _post_slack(webhook_url: str, payload: dict) -> bool:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return 200 <= resp.status < 300
    except urllib.error.URLError as e:
        logger.warning("Slack webhook URLError: %s", e)
        return False
    except Exception:
        logger.exception("Slack webhook unexpected error")
        return False


def _build_slack_blocks(triggered: list[tuple[Rule, dict]]) -> dict:
    blocks: list[dict] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🚨 Academy Dev Alerts"},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f"_{timezone.now().isoformat(timespec='seconds')}_"}],
        },
        {"type": "divider"},
    ]
    for rule, data in triggered:
        title = data.get("title") or rule.label
        rows: list[dict] = data.get("rows") or []
        sample = rows[:5]
        body_lines = []
        for r in sample:
            body_lines.append("• " + " · ".join(f"{k}={v}" for k, v in r.items() if v is not None and v != ""))
        if len(rows) > len(sample):
            body_lines.append(f"… (+{len(rows) - len(sample)} more)")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*{title}*\n" + "\n".join(body_lines or ["—"])},
        })
    return {"blocks": blocks, "text": "Academy Dev Alerts"}


def _normalize_phone(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _mask_phone(value: str) -> str:
    return f"{value[:3]}****{value[-4:]}" if len(value) >= 7 else "****"


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    result: list[str] = []
    used = 0
    for char in value:
        size = len(char.encode("utf-8"))
        if used + size > max_bytes:
            break
        result.append(char)
        used += size
    return "".join(result)


def _sms_count(value: object) -> int:
    try:
        return max(0, min(int(value or 0), 10000))
    except (TypeError, ValueError):
        return 0


def _sms_count_text(value: object) -> str:
    count = _sms_count(value)
    return "9999+" if count >= 10000 else str(count)


def _sms_tenant_label(tenant_id: object, tenant_code: object) -> str:
    try:
        tenant_token = f"T{int(tenant_id)}" if tenant_id is not None else "공용"
    except (TypeError, ValueError):
        tenant_token = "공용"
    if tenant_token == "공용":
        return tenant_token

    # Tenant.name is owner-editable and cannot cross the SMS trust boundary.
    # Only the platform-issued, unique ASCII code may accompany the FK id.
    code = str(tenant_code or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,31}", code):
        return tenant_token
    if re.search(r"(?:\d[_-]*){7,}\d", code):
        return tenant_token
    suffix = f"#{int(tenant_id)}"
    code = _truncate_utf8(
        code,
        max(0, SMS_TENANT_LABEL_MAX_BYTES - len(suffix.encode("utf-8"))),
    ).rstrip("_-")
    return f"{code}{suffix}" if code else tenant_token


def _sms_reason(row: dict) -> str:
    source = str(row.get("source") or "")
    if source == "backend":
        try:
            status = int(row.get("status"))
        except (TypeError, ValueError):
            status = 0
        return f"서버{status}" if 500 <= status <= 599 else "서버5xx"
    return {
        "frontend": "화면오류",
        "report": "직접신고",
        "bug_post": "버그제보",
    }.get(source, "오류")


def _incident_sms_groups(data: dict) -> list[dict]:
    groups: dict[object, dict] = {}
    for row in data.get("rows") or []:
        tenant_id = row.get("tenant_id")
        key = tenant_id if tenant_id is not None else "public"
        group = groups.setdefault(
            key,
            {
                "tenant_id": tenant_id,
                "label": _sms_tenant_label(
                    tenant_id,
                    row.get("tenant") or row.get("tenant_code"),
                ),
                "reasons": {},
                "total": 0,
            },
        )
        count = _sms_count(row.get("count"))
        reason = _sms_reason(row)
        group["reasons"][reason] = group["reasons"].get(reason, 0) + count
        group["total"] += count
    return sorted(
        groups.values(),
        key=lambda group: (
            -group["total"],
            group["tenant_id"] is None,
            group["tenant_id"] or 0,
        ),
    )


def _build_user_incident_sms_payload(data: dict) -> tuple[str, dict]:
    groups = _incident_sms_groups(data)
    total = _sms_count(data.get("total"))
    tenant_count = len(groups)
    header = f"[학원+] 오류{_sms_count_text(total)}건/{tenant_count}곳"
    footer = "/dev"
    candidates: list[str] = []
    for group in groups:
        reasons = sorted(
            group["reasons"].items(),
            key=lambda item: (-item[1], item[0]),
        )
        primary_reason, primary_count = reasons[0]
        extra = f"+{len(reasons) - 1}종" if len(reasons) > 1 else ""
        candidates.append(
            f"{group['label']}:{primary_reason}({_sms_count_text(primary_count)}){extra}"
        )

    displayed: list[str] = []
    for index, candidate in enumerate(candidates):
        proposed = " ".join([*displayed, candidate])
        remaining = len(candidates) - index - 1
        overflow = f" +{remaining}곳" if remaining else ""
        proposed_text = f"{header}\n{proposed}{overflow}\n{footer}"
        if len(proposed_text.encode("utf-8")) > SMS_MAX_BYTES:
            break
        displayed.append(candidate)

    if not displayed and groups:
        group = groups[0]
        primary_reason = sorted(
            group["reasons"].items(),
            key=lambda item: (-item[1], item[0]),
        )[0][0]
        displayed = [
            f"{'T' + str(group['tenant_id']) if group['tenant_id'] else '공용'}:"
            f"{primary_reason}"
        ]

    omitted = max(0, tenant_count - len(displayed))
    detail = " ".join(displayed)
    if omitted:
        detail = f"{detail} +{omitted}곳"
    text = f"{header}\n{detail}\n{footer}"
    if len(text.encode("utf-8")) > SMS_MAX_BYTES:
        text = (
            f"[학원+] 오류{_sms_count_text(total)}건\n"
            f"{displayed[0] if displayed else '공용:오류'}\n{footer}"
        )
    if len(text.encode("utf-8")) > SMS_MAX_BYTES:
        text = f"[학원+] 오류{_sms_count_text(total)}건\n테넌트 상세\n{footer}"

    return text, {
        "tenant_count": tenant_count,
        "displayed_tenant_count": len(displayed),
        "omitted_tenant_count": omitted,
        "body_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _build_user_incident_sms(data: dict) -> str:
    text, _metadata = _build_user_incident_sms_payload(data)
    return text


def _get_solapi_client():
    from apps.domains.messaging.services.solapi_client import get_solapi_client

    return get_solapi_client()


def _send_ops_sms(text: str) -> dict:
    recipient = _normalize_phone(getattr(settings, "DEV_ALERTS_SMS_RECIPIENT", ""))
    if recipient != CONTROLLED_OPS_PHONE:
        return {
            "status": "error",
            "reason": (
                "recipient_not_allowed:"
                f"{_mask_phone(recipient) if recipient else 'unset'}"
            ),
        }
    sender = _normalize_phone(getattr(settings, "SOLAPI_SENDER", ""))
    if sender != CONTROLLED_OPS_PHONE:
        return {
            "status": "error",
            "reason": (
                "sender_not_allowed:"
                f"{_mask_phone(sender) if sender else 'unset'}"
            ),
        }
    if not text or len(text.encode("utf-8")) > 90:
        return {"status": "error", "reason": "sms_text_must_be_1_to_90_bytes"}

    client = _get_solapi_client()
    if client is None:
        return {"status": "error", "reason": "solapi_client_unavailable"}
    try:
        from solapi.model import RequestMessage
        from solapi.model.message_type import MessageType

        response = client.send(
            RequestMessage(
                from_=sender,
                to=recipient,
                text=text,
                type=MessageType.SMS,
            )
        )
        group_info = getattr(response, "group_info", None)
        group_id = str(getattr(group_info, "group_id", "") or "")
        count = getattr(group_info, "count", None)
        registered_success = int(getattr(count, "registered_success", 0) or 0)
        registered_failed = int(getattr(count, "registered_failed", 0) or 0)
        if not group_id or registered_success < 1 or registered_failed:
            return {
                "status": "error",
                "reason": (
                    "provider_registration_failed:"
                    f"success={registered_success},failed={registered_failed}"
                ),
                "group_id": group_id,
            }
        return {"status": "ok", "group_id": group_id}
    except Exception as exc:
        logger.exception("Operator incident SMS send failed")
        return {"status": "error", "reason": str(exc)[:255]}


def _verify_ops_sms_delivery(group_id: str, wait_seconds: int) -> dict:
    client = _get_solapi_client()
    if client is None:
        return {"status": "error", "reason": "solapi_client_unavailable"}
    deadline = time.monotonic() + max(0, wait_seconds)
    last = {
        "sent_total": 0,
        "sent_success": 0,
        "sent_pending": 0,
        "registered_failed": 0,
    }
    while time.monotonic() <= deadline:
        try:
            group = client.get_group(group_id)
            count = group.count
            last = {
                "sent_total": int(count.sent_total or 0),
                "sent_success": int(count.sent_success or 0),
                "sent_pending": int(count.sent_pending or 0),
                "registered_failed": int(count.registered_failed or 0),
            }
            if last["sent_success"] >= 1:
                return {"status": "ok", **last}
            if last["registered_failed"] > 0:
                return {"status": "error", "reason": "provider_delivery_failed", **last}
            if last["sent_total"] >= 1 and last["sent_pending"] == 0:
                return {"status": "error", "reason": "provider_delivery_failed", **last}
        except Exception as exc:
            logger.warning("Operator SMS delivery lookup failed: %s", exc)
            last = {**last, "lookup_error": str(exc)[:120]}
        if time.monotonic() + 3 > deadline:
            break
        time.sleep(3)
    return {"status": "error", "reason": "provider_delivery_timeout", **last}


def _provider_delivery_state(result: dict) -> str:
    if result.get("status") == "ok":
        return "delivered"
    reason = str(result.get("reason") or "")
    if reason.startswith("provider_registration_failed"):
        return "registration_failed"
    if not result.get("group_id"):
        if reason in {
            "sender_required",
            "solapi_client_unavailable",
        } or reason.startswith(("recipient_not_allowed", "sender_not_allowed")):
            return "registration_failed"
        return "ambiguous"
    if (
        _sms_count(result.get("registered_failed")) > 0
        or (
            _sms_count(result.get("sent_total")) > 0
            and _sms_count(result.get("sent_pending")) == 0
        )
    ):
        return "definitive_failure"
    return "ambiguous"


def _provider_delivery_snapshot(result: dict) -> dict:
    return {
        key: result.get(key)
        for key in (
            "status",
            "reason",
            "sent_total",
            "sent_success",
            "sent_pending",
            "registered_failed",
        )
        if result.get(key) is not None
    }


def _create_sms_attempt(
    data: dict,
    sms_metadata: dict | None = None,
):
    from apps.core.models import OpsAuditLog

    return OpsAuditLog.objects.create(
        action=SMS_DELIVERY_ACTION,
        summary=f"User incident SMS attempt ({data.get('total', 0)} events)",
        payload={
            "fingerprints": list(data.get("fingerprints") or []),
            "event_count": _sms_count(data.get("total")),
            "recipient_last4": CONTROLLED_OPS_PHONE[-4:],
            "provider_group_id": "",
            "attempt_state": "created",
            **(sms_metadata or {}),
        },
        result="failed",
        error="provider_attempt_pending",
    )


def _record_sms_delivery(attempt, result: dict) -> None:
    state = _provider_delivery_state(result)
    ok = state == "delivered"
    payload = dict(attempt.payload or {})
    payload.update(
        {
            "provider_group_id": result.get("group_id") or "",
            "attempt_state": state,
            "provider_delivery": _provider_delivery_snapshot(result),
        }
    )
    attempt.summary = (
        f"User incident SMS {'sent' if ok else state} "
        f"({payload.get('event_count', 0)} events)"
    )
    attempt.payload = payload
    attempt.result = "success" if ok else "failed"
    attempt.error = "" if ok else str(result.get("reason") or state)[:255]
    attempt.save()


def _record_sms_registration(attempt, registration: dict) -> None:
    payload = dict(attempt.payload or {})
    payload.update(
        {
            "provider_group_id": registration.get("group_id") or "",
            "attempt_state": "registered",
            "provider_delivery": _provider_delivery_snapshot(registration),
        }
    )
    attempt.summary = (
        "User incident SMS registered "
        f"({payload.get('event_count', 0)} events)"
    )
    attempt.payload = payload
    attempt.error = "provider_delivery_pending"
    attempt.save()


def _reconcile_unresolved_sms_attempts() -> None:
    """Recheck accepted groups; unresolved fingerprints remain held, never resent."""
    from apps.core.models import OpsAuditLog

    since = timezone.now() - timedelta(days=INCIDENT_RETENTION_DAYS)
    attempts = OpsAuditLog.objects.filter(
        action=SMS_DELIVERY_ACTION,
        result="failed",
        created_at__gte=since,
    ).order_by("updated_at", "id")
    checked = 0
    for attempt in attempts:
        payload = attempt.payload or {}
        state = str(payload.get("attempt_state") or "")
        if state in {"registration_failed", "definitive_failure"}:
            continue
        group_id = str(payload.get("provider_group_id") or "")
        if not group_id:
            continue
        if checked >= SMS_MAX_RECONCILIATIONS_PER_RUN:
            break
        checked += 1
        delivery = _verify_ops_sms_delivery(group_id, 0)
        result = {**delivery, "group_id": group_id}
        _record_sms_delivery(attempt, result)


def _incident_sms_rate_limit_reason() -> str:
    from apps.core.models import OpsAuditLog

    now = timezone.now()
    attempts = OpsAuditLog.objects.filter(
        action=SMS_DELIVERY_ACTION,
        created_at__gte=now - timedelta(hours=1),
    ).order_by("-created_at")
    if attempts.count() >= SMS_MAX_ATTEMPTS_PER_HOUR:
        return "hourly_attempt_cap"
    last_attempt_at = attempts.values_list("created_at", flat=True).first()
    if (
        last_attempt_at
        and now - last_attempt_at < timedelta(minutes=SMS_RETRY_COOLDOWN_MINUTES)
    ):
        return "retry_cooldown"
    return ""


def _record_sms_rate_limit(data: dict, reason: str) -> None:
    from apps.core.models import OpsAuditLog

    OpsAuditLog.objects.create(
        action=SMS_RATE_LIMIT_ACTION,
        summary=f"User incident SMS deferred ({reason})",
        payload={
            "reason": reason,
            "fingerprint_count": len(data.get("fingerprints") or []),
            "event_count": _sms_count(data.get("total")),
        },
        result="failed",
        error=reason,
    )


def _record_cron_invocation(opts: dict, *, result: str, error: str = "") -> None:
    """Scheduled/manual command 실행 결과를 /dev 감사 로그에 남긴다."""
    try:
        from apps.core.models import OpsAuditLog

        selected_rules = list(opts.get("rule") or [])
        OpsAuditLog.objects.create(
            action="cron.check_dev_alerts",
            summary=f"check_dev_alerts {result}",
            payload={
                "rules": selected_rules or ["all"],
                "dry_run": bool(opts.get("dry_run")),
                "test_sms": bool(opts.get("test_sms")),
            },
            result=result,
            error=error[:255],
        )
    except Exception:
        logger.exception("check_dev_alerts invocation audit failed")


@contextmanager
def _sms_delivery_lock():
    """Manual dispatch와 scheduled dispatch가 겹쳐도 같은 묶음을 두 번 보내지 않는다."""
    if connection.vendor != "postgresql":
        yield True
        return

    lock_id = 2026072501
    acquired = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(%s)", [lock_id])
            acquired = bool(cursor.fetchone()[0])
        yield acquired
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])


# ── Command ──

class Command(BaseCommand):
    help = "/dev 운영 알림 룰 평가 + Slack webhook 및 사용자 오류 운영자 SMS 전송"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="평가 결과만 출력 (Slack/SMS 전송 X).",
        )
        parser.add_argument("--silent", action="store_true", help="트리거 없으면 종료 코드 0, 무출력.")
        parser.add_argument(
            "--rule", action="append", default=[],
            help="이 옵션을 반복하면 해당 룰만 평가 (기본: 전체).",
        )
        parser.add_argument(
            "--test-sms",
            action="store_true",
            help="통제번호로 운영자 SMS 1건을 보내고 Solapi 최종 성공을 확인.",
        )
        parser.add_argument(
            "--wait-seconds",
            type=int,
            default=120,
            help="--test-sms provider 최종 상태 대기 시간 (기본 120초).",
        )
        parser.add_argument(
            "--external-signal",
            choices=EXTERNAL_SIGNAL_CHOICES,
            help="DB와 독립된 화이트리스트 운영 신호를 통제번호로 실발송.",
        )

    def handle(self, *args, **opts):
        try:
            result = self._handle(*args, **opts)
        except Exception as exc:
            _record_cron_invocation(opts, result="failed", error=str(exc))
            raise
        _record_cron_invocation(opts, result="success")
        return result

    def _handle(self, *args, **opts):
        dry_run = opts["dry_run"]
        silent = opts["silent"]
        if opts["external_signal"]:
            if dry_run or opts["test_sms"]:
                raise CommandError(
                    "--external-signal은 --dry-run/--test-sms와 함께 사용할 수 없습니다."
                )
            self._run_external_signal_sms(
                signal=opts["external_signal"],
                wait_seconds=max(0, int(opts["wait_seconds"])),
            )
            return
        if opts["test_sms"]:
            if dry_run:
                raise CommandError("--test-sms와 --dry-run은 함께 사용할 수 없습니다.")
            self._run_test_sms(wait_seconds=max(0, int(opts["wait_seconds"])))
            return

        only = set(opts["rule"] or [])
        rules = [r for r in RULES if not only or r.key in only]

        sms_enabled = bool(getattr(settings, "DEV_ALERTS_SMS_ENABLED", False))
        if (
            not dry_run
            and sms_enabled
            and any(rule.key == "user_incidents" for rule in rules)
        ):
            with _sms_delivery_lock() as acquired:
                if acquired:
                    _reconcile_unresolved_sms_attempts()

        triggered: list[tuple[Rule, dict]] = []
        for rule in rules:
            try:
                result = rule.evaluate()
            except Exception as e:
                logger.exception("Rule %s evaluate failed", rule.key)
                self.stdout.write(self.style.ERROR(f"[{rule.key}] error: {e}"))
                if rule.key == "user_incidents":
                    raise CommandError(
                        f"사용자 오류 모니터링 룰 평가 실패: {e}"
                    ) from e
                continue
            if result:
                triggered.append((rule, result))

        if not triggered:
            if not silent:
                self.stdout.write(self.style.SUCCESS("All clear — no rules triggered."))
            return

        # 콘솔 출력
        for rule, data in triggered:
            self.stdout.write(self.style.WARNING(f"\n[{rule.key}] {data.get('title')}"))
            for r in (data.get("rows") or [])[:10]:
                self.stdout.write("  " + json.dumps(r, ensure_ascii=False))

        webhook_url = (getattr(settings, "DEV_ALERTS_WEBHOOK_URL", "") or "").strip()
        if dry_run:
            self.stdout.write(self.style.NOTICE("\n--dry-run: Slack/SMS 전송 생략."))
            return

        if webhook_url:
            payload = _build_slack_blocks(triggered)
            ok = _post_slack(webhook_url, payload)
            if ok:
                self.stdout.write(
                    self.style.SUCCESS(f"\nSlack 전송 OK ({len(triggered)} rule(s)).")
                )
            else:
                self.stdout.write(self.style.ERROR("\nSlack 전송 실패."))
        else:
            self.stdout.write(self.style.NOTICE("\nDEV_ALERTS_WEBHOOK_URL 미설정 — Slack 전송 생략."))

        if not sms_enabled:
            if any(rule.key == "user_incidents" for rule, _data in triggered):
                self.stdout.write(
                    self.style.NOTICE(
                        "\nDEV_ALERTS_SMS_ENABLED=false — 사용자 오류 SMS 전송 생략."
                    )
                )
            return
        if not any(rule.key == "user_incidents" for rule, _data in triggered):
            return

        with _sms_delivery_lock() as acquired:
            if not acquired:
                self.stdout.write(
                    self.style.NOTICE("\n다른 check_dev_alerts 실행이 SMS 발송 중 — 중복 방지로 생략.")
                )
                return
            incident_data = rule_user_incidents()
            if not incident_data:
                return
            rate_limit_reason = _incident_sms_rate_limit_reason()
            if rate_limit_reason:
                _record_sms_rate_limit(incident_data, rate_limit_reason)
                self.stdout.write(
                    self.style.NOTICE(
                        "\n사용자 오류 SMS 발송 상한 적용 — 다음 주기에 재평가."
                    )
                )
                return
            sms_text, sms_metadata = _build_user_incident_sms_payload(incident_data)
            attempt = _create_sms_attempt(incident_data, sms_metadata)
            registration = _send_ops_sms(sms_text)
            group_id = str(registration.get("group_id") or "")
            if registration.get("status") == "ok":
                _record_sms_registration(attempt, registration)
                delivery = _verify_ops_sms_delivery(
                    group_id,
                    SMS_DELIVERY_WAIT_SECONDS,
                )
                result = {**delivery, "group_id": group_id}
            else:
                result = registration
            _record_sms_delivery(attempt, result)
            if result.get("status") != "ok":
                raise CommandError(
                    f"사용자 오류 SMS 전송 실패: {result.get('reason') or 'unknown'}"
                )
            self.stdout.write(
                self.style.SUCCESS(
                    "\n사용자 오류 SMS 전송 OK "
                    f"({_mask_phone(CONTROLLED_OPS_PHONE)}, "
                    f"{incident_data['total']} event(s), "
                    f"group_id={group_id}, "
                    f"sent_success={result.get('sent_success')})"
                )
            )

    def _run_test_sms(self, *, wait_seconds: int) -> None:
        if not getattr(settings, "DEV_ALERTS_SMS_ENABLED", False):
            raise CommandError("DEV_ALERTS_SMS_ENABLED=true가 필요합니다.")
        from apps.core.models import Tenant

        owner_tenant_id = getattr(settings, "OWNER_TENANT_ID", None)
        owner_tenant = (
            Tenant.objects.filter(pk=owner_tenant_id).only("id", "code").first()
            if owner_tenant_id
            else None
        )
        tenant_id = owner_tenant.id if owner_tenant else owner_tenant_id
        tenant_code = owner_tenant.code if owner_tenant else ""
        sample = _build_user_incident_sms(
            {
                "total": 1,
                "rows": [
                    {
                        "tenant_id": tenant_id,
                        "tenant_code": tenant_code,
                        "source": "backend",
                        "status": 500,
                        "count": 1,
                    }
                ],
            }
        )
        detail = sample.splitlines()[1]
        now = timezone.localtime().strftime("%Y-%m-%d %H:%M")
        text = f"[학원+] 문자테스트 {now[-5:]}\n{detail}\n/dev"
        result = _send_ops_sms(text)
        if result.get("status") != "ok":
            raise CommandError(
                f"운영자 SMS 테스트 등록 실패: {result.get('reason') or 'unknown'}"
            )
        group_id = str(result["group_id"])
        delivery = _verify_ops_sms_delivery(group_id, wait_seconds)
        from apps.core.models import OpsAuditLog

        OpsAuditLog.objects.create(
            action="alerts.user_incident_sms_test",
            summary="Operator incident SMS delivery verification",
            payload={
                "recipient_last4": CONTROLLED_OPS_PHONE[-4:],
                "provider_group_id": group_id,
                "provider_delivery": delivery,
            },
            result="success" if delivery.get("status") == "ok" else "failed",
            error=(
                ""
                if delivery.get("status") == "ok"
                else str(delivery.get("reason") or "unknown")[:255]
            ),
        )
        if delivery.get("status") != "ok":
            raise CommandError(
                f"운영자 SMS provider 최종 확인 실패: {delivery.get('reason') or 'unknown'}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                "운영자 SMS 실발송 확인 OK "
                f"({_mask_phone(CONTROLLED_OPS_PHONE)}, group_id={group_id}, "
                f"sent_success={delivery.get('sent_success')})"
            )
        )

    def _run_external_signal_sms(self, *, signal: str, wait_seconds: int) -> None:
        if not getattr(settings, "DEV_ALERTS_SMS_ENABLED", False):
            raise CommandError("DEV_ALERTS_SMS_ENABLED=true가 필요합니다.")
        texts = {
            "api_user_impact": "[학원+] 운영 장애 감지\nAPI 오류/비정상 대상\n/dev 확인",
        }
        registration = _send_ops_sms(texts[signal])
        if registration.get("status") != "ok":
            raise CommandError(
                f"외부 운영 신호 SMS 등록 실패: {registration.get('reason') or 'unknown'}"
            )
        group_id = str(registration["group_id"])
        delivery = _verify_ops_sms_delivery(group_id, wait_seconds)
        try:
            from apps.core.models import OpsAuditLog

            OpsAuditLog.objects.create(
                action="alerts.external_signal_sms",
                summary=f"External operator signal: {signal}",
                payload={
                    "signal": signal,
                    "recipient_last4": CONTROLLED_OPS_PHONE[-4:],
                    "provider_group_id": group_id,
                    "provider_delivery": delivery,
                },
                result="success" if delivery.get("status") == "ok" else "failed",
                error=(
                    ""
                    if delivery.get("status") == "ok"
                    else str(delivery.get("reason") or "unknown")[:255]
                ),
            )
        except Exception:
            logger.exception("External signal SMS audit persistence failed")
        if delivery.get("status") != "ok":
            raise CommandError(
                f"외부 운영 신호 SMS 최종 확인 실패: {delivery.get('reason') or 'unknown'}"
            )
        self.stdout.write(
            self.style.SUCCESS(
                "외부 운영 신호 SMS 확인 OK "
                f"({_mask_phone(CONTROLLED_OPS_PHONE)}, signal={signal}, "
                f"group_id={group_id}, sent_success={delivery.get('sent_success')})"
            )
        )
