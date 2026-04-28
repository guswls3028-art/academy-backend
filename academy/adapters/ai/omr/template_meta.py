# apps/worker/ai_worker/ai/omr/template_meta.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional

import requests


@dataclass(frozen=True)
class TemplateMeta:
    raw: Dict[str, Any]

    @property
    def units(self) -> str:
        return str(self.raw.get("units") or "mm")

    @property
    def page_size_mm(self) -> Tuple[float, float]:
        page = self.raw.get("page") or {}
        size = page.get("size") or {}
        return float(size.get("width") or 0.0), float(size.get("height") or 0.0)

    @property
    def questions(self) -> List[Dict[str, Any]]:
        return list(self.raw.get("questions") or [])


class TemplateMetaFetchError(RuntimeError):
    pass


def fetch_objective_meta(
    *,
    base_url: str,
    question_count: int,
    # auth options (choose what your deployment uses)
    auth_cookie_header: Optional[str] = None,
    bearer_token: Optional[str] = None,
    worker_token_header: Optional[str] = None,  # e.g. X-Worker-Token (if API allows)
    extra_headers: Optional[Dict[str, str]] = None,
    timeout: int = 10,
) -> TemplateMeta:
    """
    worker -> API (assets meta)

    Stability goals:
    - clear timeouts
    - explicit error messages
    - flexible auth (cookie/bearer/custom header)
    - returns structured exception for caller to gracefully fallback

    NOTE:
    - assets endpoint currently uses IsAuthenticated.
      Production typically uses cookie session OR bearer token.
      worker_token_header is optional if you later add internal auth for workers.
    """
    url = f"{base_url.rstrip('/')}/api/v1/assets/omr/objective/meta/"
    params = {"question_count": str(int(question_count))}

    headers: Dict[str, str] = {"Accept": "application/json"}
    if auth_cookie_header:
        headers["Cookie"] = auth_cookie_header
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if worker_token_header:
        headers["X-Worker-Token"] = str(worker_token_header)
    if extra_headers:
        for k, v in extra_headers.items():
            if k and v:
                headers[str(k)] = str(v)

    try:
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
    except Exception as e:
        raise TemplateMetaFetchError(f"meta_fetch_network_error: {e!r}") from e

    if r.status_code >= 400:
        body = (r.text or "")[:2000]
        raise TemplateMetaFetchError(
            f"meta_fetch_http_error: status={r.status_code} body={body}"
        )

    try:
        data = r.json()
    except Exception as e:
        raise TemplateMetaFetchError(f"meta_fetch_invalid_json: {e!r}") from e

    return TemplateMeta(raw=data)
