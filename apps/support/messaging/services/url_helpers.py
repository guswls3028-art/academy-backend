# apps/support/messaging/services/url_helpers.py
"""
사이트 URL 헬퍼 — get_site_url, get_tenant_site_url
"""


def get_site_url(request=None):
    """홈페이지 링크 (메시지용)"""
    from django.conf import settings
    url = getattr(settings, "SITE_URL", None)
    if url:
        return url.rstrip("/")
    if request:
        scheme = "https" if request.is_secure() else "http"
        return f"{scheme}://{request.get_host()}"
    return ""


def get_tenant_site_url(tenant) -> str:
    """
    테넌트별 사이트 URL 반환.
    테넌트의 primary domain이 있으면 https://{host}, 없으면 get_site_url() fallback.
    """
    if tenant is not None:
        try:
            domain = tenant.domains.filter(is_primary=True).first()
            if domain and domain.host:
                return f"https://{domain.host}".rstrip("/")
        except Exception:
            pass
    return get_site_url()
