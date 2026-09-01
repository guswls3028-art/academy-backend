"""PII-free, read-only Solapi final-delivery projection for one durable log."""

from __future__ import annotations

from django.utils import timezone

from apps.domains.messaging.services.solapi_client import get_solapi_client


def _message_value(message, name: str):
    value = getattr(message, name, None)
    if value is not None:
        return value
    parts = name.split("_")
    camel_name = parts[0] + "".join(part.title() for part in parts[1:])
    return getattr(message, camel_name, None)


def get_provider_delivery_status(log) -> dict[str, object]:
    checked_at = timezone.now()
    group_id = str(getattr(log, "provider_message_id", "") or "").strip()
    if not group_id:
        return {
            "status": "unavailable",
            "status_code": "",
            "checked_at": checked_at,
            "updated_at": None,
            "failure_reason": "공급사 접수 식별 정보가 없어 최종 상태를 확인할 수 없습니다.",
        }

    try:
        client = get_solapi_client()
        if client is None or not hasattr(client, "get_messages"):
            raise RuntimeError("provider_client_unavailable")
        from solapi.model.request.messages.get_messages import GetMessagesRequest

        response = client.get_messages(GetMessagesRequest(groupId=group_id, limit=20))
        messages = list((getattr(response, "message_list", None) or {}).values())
    except Exception:
        return {
            "status": "unavailable",
            "status_code": "",
            "checked_at": checked_at,
            "updated_at": None,
            "failure_reason": "공급사 최종 상태를 확인하지 못했습니다. 접수 기록은 그대로 유지됩니다.",
        }

    if not messages:
        return {
            "status": "unavailable",
            "status_code": "",
            "checked_at": checked_at,
            "updated_at": None,
            "failure_reason": "공급사에서 해당 접수 건의 최종 상태를 찾지 못했습니다.",
        }

    statuses = {str(_message_value(message, "status") or "").upper() for message in messages}
    statuses.discard("")
    codes = {str(_message_value(message, "status_code") or "") for message in messages}
    codes.discard("")
    updated_values = [
        value
        for message in messages
        for value in (
            _message_value(message, "date_updated"),
            _message_value(message, "date_reported"),
        )
        if value is not None
    ]
    updated_at = max(updated_values, key=str) if updated_values else None
    status_code = ",".join(sorted(codes))

    # The current Solapi Python response model drops the documented `status`
    # field, so statusCode remains the production-safe source of truth. 2000
    # means accepted and 3000 means the carrier report is still pending.
    if (statuses and statuses != {"COMPLETE"}) or codes.intersection({"2000", "3000"}) or not codes:
        return {
            "status": "provider_accepted",
            "status_code": status_code,
            "checked_at": checked_at,
            "updated_at": updated_at,
            "failure_reason": "",
        }
    if codes == {"4000"}:
        return {
            "status": "delivered",
            "status_code": status_code,
            "checked_at": checked_at,
            "updated_at": updated_at,
            "failure_reason": "",
        }
    return {
        "status": "failed",
        "status_code": status_code,
        "checked_at": checked_at,
        "updated_at": updated_at,
        "failure_reason": "공급사가 최종 전달 실패로 보고했습니다.",
    }
