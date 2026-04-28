# PATH: apps/support/video/cdn/cloudflare_signing.py

from __future__ import annotations

import base64
import hmac
import hashlib
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urlencode


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


@dataclass(frozen=True)
class CloudflareSignedURL:
    """
    CDN Edge(Cloudflare Worker 등)에서 검증 가능한 쿼리 서명 생성.
    - sig = HMAC-SHA256(secret, f"{path}|{exp}|{kid}|{uid}")
    - 쿼리: exp, sig, kid, uid(옵션)

    주의:
    - 백엔드는 "생성"만 담당
    - 검증/차단은 CDN 레이어에서 수행
    """
    secret: str
    key_id: str = "v1"

    def sign(self, *, path: str, expires_at: int, user_id: Optional[int] = None) -> Dict[str, str]:
        p = path if path.startswith("/") else f"/{path}"
        uid = "" if user_id is None else str(int(user_id))

        msg = f"{p}|{int(expires_at)}|{self.key_id}|{uid}".encode("utf-8")
        mac = hmac.new(self.secret.encode("utf-8"), msg, hashlib.sha256).digest()

        params = {
            "exp": str(int(expires_at)),
            "sig": _b64url(mac),
            "kid": self.key_id,
        }
        if user_id is not None:
            params["uid"] = str(int(user_id))
        return params

    def build_url(
        self,
        *,
        cdn_base: str,
        path: str,
        expires_at: int,
        user_id: Optional[int] = None,
        extra_query: Optional[Dict[str, str]] = None,
    ) -> str:
        base = (cdn_base or "").rstrip("/")
        p = path if path.startswith("/") else f"/{path}"

        q = {}
        if extra_query:
            q.update({k: str(v) for k, v in extra_query.items()})

        q.update(self.sign(path=p, expires_at=expires_at, user_id=user_id))
        return f"{base}{p}?{urlencode(q)}"
