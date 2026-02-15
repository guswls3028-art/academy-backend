# apps/domains/ai/safe.py
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def safe_dispatch(fn, *, fallback: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]:
    """
    AI Job 발행을 안전하게 감싼다.
    API는 실패해도 깨지면 안 된다. 실패 시 fallback에 error 메시지를 합쳐 반환.
    """
    try:
        return fn(**kwargs)
    except Exception as e:
        logger.exception("AI job dispatch failed", exc_info=e)
        base = dict(fallback) if fallback else {}
        base["ok"] = False
        base["error"] = str(e)
        return base
