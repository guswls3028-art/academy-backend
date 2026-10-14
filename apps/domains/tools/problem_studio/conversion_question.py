"""Question validation shared by the API and the dependency-minimal AI worker."""
from __future__ import annotations

import copy
import math

FIELDS = {"prompt": 12000, "answer": 2000, "explanation": 16000}


def validate_question(raw, original):
    if not isinstance(raw, dict):
        raise ValueError("문항 내용을 확인해 주세요.")
    result = copy.deepcopy(original)
    for key, limit in FIELDS.items():
        if key in raw:
            if not isinstance(raw[key], str) or len(raw[key]) > limit:
                raise ValueError(f"{key} 내용이 너무 길거나 올바르지 않습니다.")
            result[key] = raw[key]
    if "choices" in raw:
        choices = raw["choices"]
        if not isinstance(choices, list) or len(choices) > 20 or any(not isinstance(c, str) or len(c) > 2000 for c in choices):
            raise ValueError("보기는 20개 이하의 글로 입력해 주세요.")
        result["choices"] = choices
    if "crop" in raw:
        crop = raw["crop"]
        if crop is not None:
            if not isinstance(crop, list) or len(crop) != 4 or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in crop):
                raise ValueError("그림 범위를 확인해 주세요.")
            x, y, width, height = crop
            if min(x, y) < 0 or max(x, y) >= 1 or min(width, height) <= 0 or x + width > 1.00001 or y + height > 1.00001:
                raise ValueError("그림 범위가 원본을 벗어났습니다.")
        result["crop"] = crop
    if "page_index" in raw:
        result["page_index"] = raw["page_index"]
    if result.get("crop") != original.get("crop") or result.get("page_index") != original.get("page_index"):
        result.pop("redrawn", None)
    result["reviewed"] = False
    if original.get("id"):
        result["analysis_complete"] = True
    result.pop("proposal", None)
    return result
