import base64
import json
from typing import Dict
from django.conf import settings


# =========================
# Public API
# =========================

def build_signed_cookies_for_path(*, path_prefix: str, expires_at: int) -> Dict[str, str]:
    """
    DEV/LOCAL:
      - CloudFront 사용 안 함 → 빈 dict 반환

    PROD:
      - CloudFront Signed Cookies 생성
    """
    if settings.DEBUG:
        return {}

    key_pair_id = _required("CLOUDFRONT_KEY_PAIR_ID")
    domain = _required("CLOUDFRONT_DOMAIN").rstrip("/")

    resource = f"https://{domain}{path_prefix}*"

    policy = {
        "Statement": [{
            "Resource": resource,
            "Condition": {
                "DateLessThan": {"AWS:EpochTime": int(expires_at)}
            }
        }]
    }

    policy_json = json.dumps(policy, separators=(",", ":")).encode("utf-8")

    from botocore.signers import CloudFrontSigner

    signer = CloudFrontSigner(key_pair_id, _rsa_signer)

    signed_policy = _b64_urlsafe(policy_json)
    signature = signer._sign(policy_json)

    return {
        "CloudFront-Policy": signed_policy,
        "CloudFront-Signature": _b64_urlsafe(signature),
        "CloudFront-Key-Pair-Id": key_pair_id,
    }


def default_cookie_options(*, path_prefix: str) -> dict:
    """
    DEV: cookie 설정 안 함
    PROD: CloudFront 도메인 쿠키 설정
    """
    if settings.DEBUG:
        return {}

    domain = _required("CLOUDFRONT_DOMAIN")

    secure = bool(getattr(settings, "SESSION_COOKIE_SECURE", False))

    return {
        "domain": domain,
        "path": path_prefix,
        "httponly": True,
        "secure": secure,
        "samesite": "Lax",
    }


# =========================
# Internal helpers
# =========================

def _required(name: str) -> str:
    v = getattr(settings, name, None)
    if not v:
        raise RuntimeError(f"Missing setting: {name}")
    return str(v)


def _load_private_key_pem() -> bytes:
    pem = _required("CLOUDFRONT_PRIVATE_KEY_PEM")
    pem = pem.replace("\\n", "\n")
    return pem.encode("utf-8")


def _rsa_signer(message: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
    from cryptography.hazmat.primitives.asymmetric import padding

    key = load_pem_private_key(_load_private_key_pem(), password=None)
    return key.sign(message, padding.PKCS1v15(), hashes.SHA1())


def _b64_urlsafe(data: bytes) -> str:
    return (
        base64.b64encode(data)
        .decode("utf-8")
        .replace("+", "-")
        .replace("=", "_")
        .replace("/", "~")
    )
