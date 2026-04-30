# PATH: apps/core/management/commands/check_dev_alerts.py
"""
/dev 운영 알림 룰.

화이트리스트 룰을 평가해서 임계치 초과 시 Slack incoming webhook으로 전송한다.
크론에서 호출: python manage.py check_dev_alerts [--dry-run] [--silent]

Webhook 설정:
  DEV_ALERTS_WEBHOOK_URL=https://hooks.slack.com/services/...
  비어 있으면 전송 생략 (조건 평가 + stdout만).
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Sum
from django.utils import timezone

logger = logging.getLogger(__name__)


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
    """미답변 + 생성된지 N시간 이상 경과한 BUG/FB."""
    try:
        from apps.domains.community.models.post import PostEntity
    except Exception:
        return None
    from django.db.models import Count, Q
    since = timezone.now() - timedelta(hours=min_age_hours)
    qs = (
        PostEntity.objects.filter(post_type="board", created_at__lte=since)
        .filter(Q(title__startswith="[BUG]") | Q(title__startswith="[FB]"))
        .annotate(_rc=Count("replies"))
        .filter(_rc=0)
        .select_related("tenant")
        .order_by("-created_at")
    )
    rows = [
        {
            "tenant": p.tenant.code if p.tenant else "—",
            "title": (p.title or "")[:60],
            "at": p.created_at.isoformat() if p.created_at else None,
        }
        for p in qs[:30]
    ]
    if not rows:
        return None
    return {
        "title": f"📬 24h+ 미답변 문의 {len(rows)}건",
        "rows": rows,
        "total": len(rows),
    }


def rule_stale_workers(min_age_minutes: int = 5):
    """N분+ heartbeat 미갱신 워커. SQS 워커 process 멈춤 즉시 감지."""
    try:
        from apps.core.models import WorkerHeartbeatModel
    except Exception:
        return None
    cutoff = timezone.now() - timedelta(minutes=min_age_minutes)
    qs = WorkerHeartbeatModel.objects.filter(last_seen_at__lt=cutoff).order_by("name", "instance")
    rows = [
        {
            "worker": h.name,
            "instance": h.instance,
            "last_seen": h.last_seen_at.isoformat(timespec="seconds") if h.last_seen_at else None,
            "version": h.version or "—",
        }
        for h in qs[:30]
    ]
    if not rows:
        return None
    return {
        "title": f"💔 워커 heartbeat 정지 {len(rows)}건 ({min_age_minutes}분+ 미갱신)",
        "rows": rows,
        "total": len(rows),
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
    Rule("expiring_3d", "만료 3일 이내", rule_expiring_3d, "warning"),
    Rule("overdue_invoices", "연체/실패 인보이스", rule_overdue_invoices, "danger"),
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


# ── Command ──

class Command(BaseCommand):
    help = "/dev 콘솔 운영 알림 룰 평가 + Slack webhook 전송"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="평가 결과만 출력 (Slack 전송 X).")
        parser.add_argument("--silent", action="store_true", help="트리거 없으면 종료 코드 0, 무출력.")
        parser.add_argument(
            "--rule", action="append", default=[],
            help="이 옵션을 반복하면 해당 룰만 평가 (기본: 전체).",
        )

    def handle(self, *args, **opts):
        dry_run = opts["dry_run"]
        silent = opts["silent"]
        only = set(opts["rule"] or [])
        rules = [r for r in RULES if not only or r.key in only]

        triggered: list[tuple[Rule, dict]] = []
        for rule in rules:
            try:
                result = rule.evaluate()
            except Exception as e:
                logger.exception("Rule %s evaluate failed", rule.key)
                self.stdout.write(self.style.ERROR(f"[{rule.key}] error: {e}"))
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
            self.stdout.write(self.style.NOTICE("\n--dry-run: Slack 전송 생략."))
            return
        if not webhook_url:
            self.stdout.write(self.style.NOTICE("\nDEV_ALERTS_WEBHOOK_URL 미설정 — Slack 전송 생략."))
            return

        payload = _build_slack_blocks(triggered)
        ok = _post_slack(webhook_url, payload)
        if ok:
            self.stdout.write(self.style.SUCCESS(f"\nSlack 전송 OK ({len(triggered)} rule(s))."))
        else:
            self.stdout.write(self.style.ERROR("\nSlack 전송 실패."))
