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


def fetch_objective_meta(
    *,
    base_url: str,
    question_count: int,
    auth_cookie_header: Optional[str] = None,
    timeout: int = 10,
) -> TemplateMeta:
    """
    worker -> API (assets meta)
    - 외부 SaaS 호출 금지: 내부 API만 호출
    - auth 방식은 운영 환경에 맞게 header/cookie를 전달
    """
    url = f"{base_url.rstrip('/')}/api/v1/assets/omr/objective/meta/"
    params = {"question_count": str(int(question_count))}

    headers: Dict[str, str] = {}
    if auth_cookie_header:
        headers["Cookie"] = auth_cookie_header

    r = requests.get(url, params=params, headers=headers, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return TemplateMeta(raw=data)
