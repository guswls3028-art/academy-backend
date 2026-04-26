# PATH: apps/core/views/dev_automation.py
"""
/dev 자동화 콘솔: 감사 로그 조회 + 화이트리스트 크론 트리거.
"""
import io
import logging
import threading
from datetime import datetime
from typing import Any, Dict

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.models import OpsAuditLog
from apps.core.permissions import IsPlatformAdmin
from apps.core.services.ops_audit import record_audit

logger = logging.getLogger(__name__)


# 화이트리스트: dev 콘솔에서 트리거 허용된 management 명령들
ALLOWED_COMMANDS: Dict[str, Dict[str, Any]] = {
    "cleanup_e2e_residue": {
        "label": "E2E 잔재 정리",
        "description": "Tenant 1의 E2E 테스트 잔재 데이터 정리.",
        "default_args": ["--dry-run"],
        "destructive_args": [],
        "danger": False,
    },
    "cleanup_orphan_video_storage": {
        "label": "R2 오펀 영상 정리",
        "description": "어떤 Video에도 매칭되지 않는 R2 오브젝트 정리 (HLS/RAW orphan).",
        "default_args": ["--dry-run"],
        "destructive_args": [],
        "danger": False,
    },
    "check_dev_alerts": {
        "label": "운영 알림 룰 점검",
        "description": "만료/연체/실패/미답변 룰 평가 후 Slack webhook 전송. --dry-run으로 전송 없이 평가만.",
        "default_args": [],
        "destructive_args": [],
        "danger": False,
    },
}


class DevAuditLogListView(APIView):
    """
    GET /api/v1/core/dev/audit/?action=&actor=&tenant_code=&result=&since=&until=&limit=100
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    MAX_LIMIT = 500

    def get(self, request):
        qs = OpsAuditLog.objects.select_related("target_tenant", "target_user").order_by("-created_at")

        action = (request.query_params.get("action") or "").strip()
        actor = (request.query_params.get("actor") or "").strip()
        tenant_code = (request.query_params.get("tenant_code") or "").strip()
        result = (request.query_params.get("result") or "").strip()
        since = (request.query_params.get("since") or "").strip()
        until = (request.query_params.get("until") or "").strip()

        if action:
            qs = qs.filter(action__icontains=action)
        if actor:
            qs = qs.filter(actor_username__icontains=actor)
        if tenant_code:
            qs = qs.filter(target_tenant__code__iexact=tenant_code)
        if result in ("success", "failed"):
            qs = qs.filter(result=result)
        if since:
            try:
                qs = qs.filter(created_at__gte=datetime.fromisoformat(since))
            except ValueError:
                pass
        if until:
            try:
                qs = qs.filter(created_at__lte=datetime.fromisoformat(until))
            except ValueError:
                pass

        try:
            limit = max(1, min(self.MAX_LIMIT, int(request.query_params.get("limit") or 100)))
        except (TypeError, ValueError):
            limit = 100

        rows = list(qs[:limit].values(
            "id", "created_at", "actor_username", "action", "summary",
            "result", "error",
            "target_tenant__code", "target_tenant__name",
            "target_user_id",
        ))

        return Response({
            "results": [
                {
                    "id": r["id"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                    "actor": r["actor_username"] or "—",
                    "action": r["action"],
                    "summary": r["summary"],
                    "result": r["result"],
                    "error": r["error"],
                    "tenant_code": r["target_tenant__code"],
                    "tenant_name": r["target_tenant__name"],
                    "target_user_id": r["target_user_id"],
                }
                for r in rows
            ],
            "count": len(rows),
            "limit": limit,
        })


class DevCronListView(APIView):
    """
    GET /api/v1/core/dev/cron/
    화이트리스트 크론 + 마지막 실행 시각.
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def get(self, request):
        items = []
        for cmd_name, meta in ALLOWED_COMMANDS.items():
            last = (
                OpsAuditLog.objects.filter(action=f"cron.{cmd_name}")
                .order_by("-created_at")
                .first()
            )
            items.append({
                "command": cmd_name,
                "label": meta["label"],
                "description": meta["description"],
                "default_args": meta["default_args"],
                "danger": meta["danger"],
                "last_run_at": last.created_at.isoformat() if last else None,
                "last_run_result": last.result if last else None,
                "last_run_summary": last.summary if last else None,
            })
        return Response({"results": items})


class DevCronTriggerView(APIView):
    """
    POST /api/v1/core/dev/cron/run/
    body: { command: "cleanup_e2e_residue", args: ["--dry-run"] | null }

    화이트리스트 명령만 허용. 비동기로 실행하고 결과는 감사 로그에 기록.
    응답은 즉시 (started=true) 반환 — 진행은 audit log로 추적.
    """
    permission_classes = [IsAuthenticated, IsPlatformAdmin]

    def post(self, request):
        cmd = (request.data or {}).get("command")
        args = (request.data or {}).get("args")

        if not cmd or cmd not in ALLOWED_COMMANDS:
            return Response({"detail": "Command not allowed."}, status=403)

        meta = ALLOWED_COMMANDS[cmd]
        run_args = list(args) if isinstance(args, list) and args else list(meta["default_args"])

        # 시작 기록
        record_audit(
            request,
            action=f"cron.{cmd}",
            summary=f"Cron triggered: {cmd} {' '.join(run_args)}",
            payload={"command": cmd, "args": run_args, "phase": "started"},
        )

        # 백그라운드 실행 (스레드) — Django call_command는 sync.
        # 실 운영에선 SQS/Celery로 보내야 하지만 dev 콘솔 수동 트리거는 thread로 충분.
        from django.core.management import call_command

        actor_meta = {"actor": getattr(request.user, "username", "")}
        # request 객체는 thread에 안전하게 넘기기 어려움 → IP/UA만 캡처해서 우회.
        ip = (request.META.get("HTTP_X_FORWARDED_FOR") or request.META.get("REMOTE_ADDR") or "")[:64]
        ua = (request.META.get("HTTP_USER_AGENT") or "")[:255]

        def _run():
            buf = io.StringIO()
            err = io.StringIO()
            try:
                call_command(cmd, *run_args, stdout=buf, stderr=err)
                tail = (buf.getvalue() or "").strip().splitlines()[-5:]
                summary = f"Cron OK: {cmd} ({' '.join(run_args)})"
                payload = {
                    "command": cmd, "args": run_args, "phase": "completed",
                    "tail": "\n".join(tail)[:1000],
                }
                _record_async(action=f"cron.{cmd}", result="success", summary=summary,
                              payload=payload, actor_username=actor_meta["actor"], ip=ip, ua=ua)
            except Exception as e:
                logger.exception("Cron failed: %s", cmd)
                _record_async(
                    action=f"cron.{cmd}", result="failed",
                    summary=f"Cron FAIL: {cmd}",
                    payload={"command": cmd, "args": run_args, "phase": "completed", "error": str(e)[:500]},
                    error=str(e)[:200],
                    actor_username=actor_meta["actor"], ip=ip, ua=ua,
                )

        t = threading.Thread(target=_run, name=f"dev-cron-{cmd}", daemon=True)
        t.start()

        return Response({
            "started": True,
            "command": cmd,
            "args": run_args,
            "started_at": timezone.now().isoformat(),
        })


def _record_async(*, action, result, summary, payload, actor_username, ip, ua, error=""):
    """request 없이 OpsAuditLog 직접 기록 (백그라운드 스레드용)."""
    try:
        OpsAuditLog.objects.create(
            actor_user=None,
            actor_username=actor_username[:150],
            action=action[:64],
            summary=summary[:255],
            target_tenant=None,
            target_user=None,
            payload=payload or {},
            result=result if result in ("success", "failed") else "success",
            error=(error or "")[:255],
            ip=ip,
            user_agent=ua,
        )
    except Exception:
        logger.exception("_record_async failed: action=%s", action)
