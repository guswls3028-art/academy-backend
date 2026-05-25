"""Compatibility helpers for OMR document assembly."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeExternalUrl(ValueError):
    """Raised when an external URL is not allowed for server-side fetching."""


def resolve_template_exam_for_tenant(exam, tenant):
    from apps.domains.exams.services.template_resolver import resolve_template_exam

    sheet_exam = resolve_template_exam(exam)
    if int(getattr(sheet_exam, "tenant_id", 0) or 0) != int(getattr(tenant, "id", 0) or 0):
        raise ValueError("template exam belongs to another tenant")
    return sheet_exam


def _is_safe_external_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme != "https":
            return False
        host = parsed.hostname or ""
        if not host:
            return False
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            return False
        for info in infos:
            ip_str = info[4][0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                return False
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            ):
                return False
        return True
    except Exception:
        return False


def fetch_public_https_bytes(url: str, *, timeout: int = 5) -> tuple[bytes, str]:
    if not _is_safe_external_url(url):
        raise UnsafeExternalUrl(url)

    import requests

    resp = requests.get(url, timeout=timeout, allow_redirects=False)
    resp.raise_for_status()
    mime = resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
    return resp.content, mime
