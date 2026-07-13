from __future__ import annotations

import re
from typing import Any

SENSITIVE_NOTIFICATION_TYPES = frozenset(
    {
        "registration_approved_student",
        "registration_approved_parent",
        "password_find_otp",
        "password_reset_student",
        "password_reset_parent",
    }
)

SENSITIVE_MESSAGE_PLACEHOLDER = "[보안] 계정/인증 알림 본문은 저장하지 않습니다."

_LEGACY_PARENT_PHONE_TARGET = re.compile(r"^(parent:[^:]+):([^:]+)$")

_SENSITIVE_LINE_MARKERS = (
    "비밀번호",
    "임시비밀번호",
    "임시 비밀번호",
    "인증번호",
    "password",
    "pw",
)


def sanitize_message_body_for_log(
    message_body: str,
    *,
    notification_type: str = "",
) -> str:
    """Return a NotificationLog-safe body with account secrets removed."""

    body = str(message_body or "")
    if not body:
        return ""

    if notification_type in SENSITIVE_NOTIFICATION_TYPES:
        return SENSITIVE_MESSAGE_PLACEHOLDER

    redacted_lines: list[str] = []
    for line in body.splitlines():
        lowered = line.lower()
        if not any(marker in lowered for marker in _SENSITIVE_LINE_MARKERS):
            redacted_lines.append(line)
            continue

        separator = "：" if "：" in line else ":"
        if separator in line:
            prefix = line.split(separator, 1)[0].rstrip()
            redacted_lines.append(f"{prefix}{separator} [보안상 숨김]")
        else:
            redacted_lines.append("[보안상 민감정보 줄 숨김]")
    return "\n".join(redacted_lines)


def sanitize_notification_target_id(target_id: Any) -> str:
    """Remove a phone suffix written by the legacy parent-account key format."""

    value = str(target_id or "")
    match = _LEGACY_PARENT_PHONE_TARGET.fullmatch(value)
    if match:
        phone_digits = re.sub(r"\D", "", match.group(2))
        if 10 <= len(phone_digits) <= 15:
            return match.group(1)
    return value


def is_sensitive_notification(
    *,
    trigger: str = "",
    payload: object = None,
) -> bool:
    """Return whether a durable delivery row can contain an account secret."""

    event_type = payload.get("event_type", "") if isinstance(payload, dict) else ""
    return trigger in SENSITIVE_NOTIFICATION_TYPES or event_type in SENSITIVE_NOTIFICATION_TYPES


def redact_terminal_delivery_payload(*, trigger: str, payload: object) -> object:
    """Drop recipient PII once a durable delivery cannot be retried.

    Retryable rows deliberately retain the complete provider payload. Non-object
    malformed rows are returned verbatim so incident responders do not lose the
    original forensic evidence.
    """

    if not isinstance(payload, dict):
        return payload

    redacted: dict[str, Any] = {"redacted": True}
    for key in (
        "tenant_id",
        "source_tenant_id",
        "event_type",
        "target_type",
        "target_id",
        "occurrence_key",
        "message_mode",
        "template_id",
    ):
        value = payload.get(key)
        if value not in (None, ""):
            redacted[key] = value
    if "target_id" in redacted:
        redacted["target_id"] = sanitize_notification_target_id(
            redacted["target_id"]
        )
    return redacted


def redact_consumed_preview_payload(payload: object) -> dict[str, Any]:
    """Keep only non-recipient metadata after a preview token is consumed."""

    source = payload if isinstance(payload, dict) else {}
    return {
        "redacted": True,
        "recipients": [],
        "notification_type": str(source.get("notification_type") or ""),
        "send_to": str(source.get("send_to") or ""),
    }
