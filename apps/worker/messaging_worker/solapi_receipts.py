"""Interpret Solapi SDK admission for the single recipient in one worker job."""


def send_solapi_message(client, message, destination):
    from solapi.error.MessageNotReceiveError import MessageNotReceivedError

    try:
        response = client.send(message)
    except MessageNotReceivedError as exc:
        # SDK 5 raises this instead of returning a group when all submissions failed.
        if not exc.failed_messages:
            raise
        return _rejected_receipt(exc.failed_messages, destination)
    return _admission_result(response, destination)

def _reference(value):
    return value if isinstance(value, str) and 0 < len(value) <= 128 and value.strip() == value else ""


def _failed_message_id(messages, destination):
    matched = [message for message in messages
               if (getattr(message, "to", None) or "").replace("-", "") == destination]
    if not matched and len(messages) == 1 and not getattr(messages[0], "to", None):
        matched = messages
    return _reference(getattr(matched[0], "message_id", None)) if len(matched) == 1 else ""


def _rejected_receipt(messages, destination, group_id=""):
    return {
        "status": "error", "reason": "alimtalk_failed_or_rejected",
        "group_id": _reference(group_id), "message_id": _failed_message_id(messages, destination),
        "provider_called": True, "provider_outcome": "rejected",
        "definitely_not_accepted": True, "provider_retryable": False,
    }


def _admission_result(response, destination):
    group = getattr(response, "group_info", None)
    group_id = _reference(getattr(group, "group_id", None))
    counts = getattr(group, "count", None)
    accepted = getattr(counts, "registered_success", None)
    failed = getattr(counts, "registered_failed", None)
    total = getattr(counts, "total", None)
    if (accepted, failed, total) == (1, 0, 1):
        messages = getattr(response, "message_list", None) or []
        message_id = _reference(messages[0].message_id) if len(messages) == 1 else ""
        return {"status": "ok", "group_id": group_id, "message_id": message_id,
                "provider_called": True, "provider_outcome": "accepted"}
    if isinstance(failed, int) and failed > 0 and accepted == 0 and total == failed:
        return _rejected_receipt(getattr(response, "failed_message_list", None) or [], destination, group_id)
    mixed = isinstance(accepted, int) and isinstance(failed, int) and accepted > 0 and failed > 0
    return {"status": "error", "reason": "provider_partial_acceptance" if mixed else "provider_unconfirmed_response",
            "group_id": group_id, "provider_called": True, "provider_outcome": "unknown"}


def receipt_reference(result):
    """Keep the raw lookup ID and the SDK query field that owns it."""
    if result.get("message_id"):
        return result["message_id"], "message"
    if result.get("group_id"):
        return result["group_id"], "group"
    legacy_id = result.get("messagekey") or result.get("refkey")
    if legacy_id:
        return str(legacy_id)[:128], None
    return "", "none"
