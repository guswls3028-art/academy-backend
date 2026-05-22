from __future__ import annotations

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
