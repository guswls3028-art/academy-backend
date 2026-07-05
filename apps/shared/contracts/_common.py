from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def contract_to_json(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)


def contract_from_json(raw: str) -> dict[str, Any]:
    return json.loads(raw)
