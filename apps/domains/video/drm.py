import time
from typing import Any, Dict, Tuple

from django.core import signing


_SALT = "media.playback.token.v1"


def _token_now() -> int:
    return int(time.time())


def create_playback_token(
    *,
    payload: Dict[str, Any],
    ttl_seconds: int | None = None,
    expires_at: int | None = None,
) -> str:
    if (ttl_seconds is None) == (expires_at is None):
        raise ValueError("exactly one playback token expiry is required")
    now = _token_now()
    data = dict(payload or {})
    data["iat"] = now
    data["exp"] = (
        int(expires_at)
        if expires_at is not None
        else now + int(ttl_seconds)
    )
    if data["exp"] <= now:
        raise ValueError("playback token expiry must be in the future")
    return signing.dumps(data, salt=_SALT, compress=True)


def verify_playback_token(token: str) -> Tuple[bool, Dict[str, Any] | None, str | None]:
    if not token:
        return False, None, "token_required"

    try:
        data = signing.loads(token, salt=_SALT)
    except signing.BadSignature:
        return False, None, "invalid_token"
    except Exception:
        return False, None, "token_decode_failed"

    try:
        exp = int(data.get("exp") or 0)
    except Exception:
        return False, None, "token_exp_invalid"

    if exp <= int(time.time()):
        return False, None, "token_expired"

    return True, data, None
