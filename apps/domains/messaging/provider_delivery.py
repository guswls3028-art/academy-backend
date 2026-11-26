"""PII-free, read-only Solapi final-delivery projection for one durable log."""

from __future__ import annotations

from django.utils import timezone

from apps.domains.messaging.security import build_recipient_fingerprint_candidates
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
    reference = str(getattr(log, "provider_message_id", "") or "").strip()
    if not reference:
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

        by_message = getattr(log, "provider_reference_kind", None) == "message"
        params = {"message_id": reference} if by_message else {"group_id": reference}
        response = client.get_messages(GetMessagesRequest(**params, limit=20))
        rows = getattr(response, "message_list", None) or {}
        if by_message:
            messages = [rows[reference]] if reference in rows else []
        elif getattr(log, "failure_reason", "") in {"provider_partial_acceptance", "provider_unconfirmed_response"}:
            # A mixed group cannot establish this recipient's outcome from another member's report.
            fingerprint = getattr(log, "recipient_fingerprint", "")
            messages = [row for row in rows.values() if fingerprint and
                        fingerprint in build_recipient_fingerprint_candidates(_message_value(row, "to"))]
            if len(messages) != 1 or getattr(response, "next_key", None):
                messages = []
        else:
            messages = list(rows.values())
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

    codes = {str(_message_value(message, "status_code") or "") for message in messages}
    if any(len(code) != 4 or not code.isascii() or not code.isdigit() for code in codes):
        return {
            "status": "unavailable", "status_code": "", "checked_at": checked_at, "updated_at": None,
            "failure_reason": "공급사 응답에서 해당 메시지의 상태 코드를 확인할 수 없습니다.",
        }
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

    if (getattr(log, "status", "") == "failed"
            and getattr(log, "failure_reason", "") == "alimtalk_failed_or_rejected"
            and codes.intersection({"2000", "3000", "4000"})):
        return {
            "status": "unavailable", "status_code": status_code, "checked_at": checked_at,
            "updated_at": updated_at, "failure_reason": "공급사 조회 결과와 기존 접수 거절 기록이 일치하지 않습니다.",
        }

    # The current Solapi Python response model drops the documented `status`
    # field, so statusCode remains the production-safe source of truth. 2000
    # means accepted and 3000 means the carrier report is still pending.
    if codes.intersection({"2000", "3000"}):
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
        "failure_reason": (
            "전송경로 없음(3058): 공급사가 해당 수신번호로 알림톡을 전달할 경로를 찾지 못했습니다. 등록된 번호를 확인해 주세요. 이 결과만으로 카카오톡 미가입이나 차단 여부를 단정할 수 없습니다."
            if codes == {"3058"}
            else "공급사가 최종 전달 실패로 보고했습니다."
        ),
    }
