from urllib.parse import urlsplit


def is_allowed_web_push_endpoint(endpoint: str) -> bool:
    """Allow only browser-vended public Web Push services."""
    try:
        parsed = urlsplit(endpoint)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except (TypeError, ValueError):
        return False
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username
        or parsed.password
        or port not in (None, 443)
    ):
        return False
    return (
        host == "fcm.googleapis.com"
        or host == "updates.push.services.mozilla.com"
        or host.endswith(".push.services.mozilla.com")
        or host.endswith(".push.apple.com")
    )
