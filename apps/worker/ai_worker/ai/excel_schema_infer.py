# PATH: apps/worker/ai_worker/ai/excel_schema_infer.py
# AI 2차 판정: parent_phone 컬럼 식별 (rule conf 0.6~0.9 시 호출)

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _mask_phone(v: str) -> str:
    """01012345678 → 010****5678"""
    raw = re.sub(r"\D", "", str(v))
    if len(raw) >= 7 and raw.startswith("010"):
        return raw[:3] + "****" + raw[-4:]
    return "***"


def infer_parent_phone_column(
    candidates: list[dict[str, Any]],
) -> tuple[int | None, float]:
    """
    AI로 parent_phone 컬럼 판정.
    candidates: [{"col_index": int, "header": str, "samples": list[str], "rule_score": float}]
    Returns: (parent_phone_col_index | None, confidence)
    """
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai not installed")

    import os

    api_key = os.getenv("OPENAI_API_KEY") or os.getenv("EMBEDDING_OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    masked = []
    for c in candidates:
        samples = c.get("samples", [])[:5]
        masked_samples = [_mask_phone(s) for s in samples]
        masked.append({
            "col_index": c["col_index"],
            "header": c["header"],
            "samples": masked_samples,
        })

    prompt = f"""You are classifying which column is the PARENT phone number in a Korean academy spreadsheet.

Rules:
- Parent phone is mandatory for student registration.
- Header with parent/guardian/학부모/부모/보호자 → strong signal for parent_phone.
- Header with student/학생 → usually student_phone, not parent.
- Korean mobile numbers start with 010 (10-11 digits).

Columns (samples are masked for privacy):
{json.dumps(masked, ensure_ascii=False)}

Return ONLY valid JSON:
{{"parent_phone_col_index": <int or null>, "confidence": <0.0 to 1.0>}}"""

    client = OpenAI(api_key=api_key)
    response = client.chat.completions.create(
        model=os.getenv("EXCEL_SCHEMA_INFER_MODEL", "gpt-4o-mini"),
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    content = response.choices[0].message.content or "{}"
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning("excel_schema_infer: invalid JSON %s", e)
        return None, 0.0

    col = data.get("parent_phone_col_index")
    conf = float(data.get("confidence", 0))
    if col is not None and isinstance(col, int):
        return col, conf
    return None, conf
