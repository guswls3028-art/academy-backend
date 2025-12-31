# apps/support/media/cdn/cloudfront.py
# ❗ CloudFront DEPRECATED
# ❗ Cloudflare CDN 사용
# ❗ Signed Cookie / RSA / hazmat 전부 비활성화

from typing import Dict


def build_signed_cookies_for_path(*, path_prefix: str, expires_at: int) -> Dict[str, str]:
    """
    Cloudflare CDN 사용
    - Signed Cookie 사용 안 함
    - 항상 빈 dict 반환
    """
    return {}


def default_cookie_options(*, path_prefix: str) -> dict:
    """
    Cloudflare CDN 사용
    - 쿠키 설정 안 함
    """
    return {}
