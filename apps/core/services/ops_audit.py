# PATH: apps/core/services/ops_audit.py
"""
플랫폼 운영 감사 로그 기록 서비스.

dev_app 뷰에서 호출:
    from apps.core.services.ops_audit import record_audit

    record_audit(
        request,
        action="tenant.update",
        target_tenant=tenant,
        summary=f"{tenant.code}: is_active=False",
        payload={"isActive": False},
    )
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _client_ip(request) -> str:
    if not request:
        return ""
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR") if request.META else None
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.META.get("REMOTE_ADDR") or "")[:64] if request.META else ""


def _user_agent(request) -> str:
    if not request or not request.META:
        return ""
    return (request.META.get("HTTP_USER_AGENT") or "")[:255]


_SENSITIVE_KEYS = {"password", "old_password", "new_password", "token", "secret", "api_key"}


def _sanitize(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {
            k: ("***" if k.lower() in _SENSITIVE_KEYS else _sanitize(v))
            for k, v in payload.items()
        }
    if isinstance(payload, list):
        return [_sanitize(v) for v in payload]
    return payload


def record_audit(
    request,
    *,
    action: str,
    summary: str = "",
    target_tenant=None,
    target_user=None,
    payload: Any = None,
    result: str = "success",
    error: str = "",
) -> None:
    """감사 로그 1건 기록. 실패해도 호출자 흐름은 끊지 않는다."""
    try:
        from apps.core.models import OpsAuditLog

        actor = getattr(request, "user", None) if request else None
        actor_user = actor if (actor and getattr(actor, "is_authenticated", False)) else None
        actor_username = ""
        if actor_user is not None:
            actor_username = (getattr(actor_user, "username", "") or "")[:150]

        OpsAuditLog.objects.create(
            actor_user=actor_user,
            actor_username=actor_username,
            action=action[:64],
            summary=(summary or "")[:255],
            target_tenant=target_tenant,
            target_user=target_user,
            payload=_sanitize(payload) if payload is not None else {},
            result=result if result in ("success", "failed") else "success",
            error=(error or "")[:255],
            ip=_client_ip(request),
            user_agent=_user_agent(request),
        )
    except Exception:
        # 감사 기록 실패가 본 작업을 깨뜨리지 않도록.
        logger.exception("record_audit failed: action=%s", action)
