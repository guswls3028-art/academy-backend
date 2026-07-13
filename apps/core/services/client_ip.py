from __future__ import annotations

import ipaddress

from django.conf import settings


def _parse_ip(value: object):
    try:
        return ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None


def _trusted_proxy_networks():
    configured = getattr(settings, "TRUSTED_PROXY_CIDRS", "") or ""
    networks = []
    for raw in str(configured).split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            networks.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            continue
    return networks


def _is_trusted(ip, networks) -> bool:
    return bool(ip and any(ip in network for network in networks))


def get_client_ip(request) -> str:
    """Resolve client IP without trusting caller-supplied XFF prefixes.

    X-Forwarded-For is considered only when REMOTE_ADDR is a configured proxy.
    The chain is walked from the trusted edge toward the client, returning the
    first untrusted hop. This matches ALB append mode and rejects forged leading
    values supplied by an internet client.
    """

    meta = getattr(request, "META", {}) or {}
    remote = _parse_ip(meta.get("REMOTE_ADDR"))
    networks = _trusted_proxy_networks()
    if not remote:
        return ""
    if not networks or not _is_trusted(remote, networks):
        return str(remote)

    forwarded = str(meta.get("HTTP_X_FORWARDED_FOR") or "")
    hops = [ip for ip in (_parse_ip(part) for part in forwarded.split(",")) if ip]
    for hop in reversed([*hops, remote]):
        if not _is_trusted(hop, networks):
            return str(hop)
    return str(hops[0] if hops else remote)
