# apps/worker/ai/omr/engine.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

from apps.worker.ai.omr.types import OMRAnswerV1


# ------------------------------------------------------------
# OMR v1 Simple Engine (CPU)
# - ROI(마킹 영역) 이미지에서 choice별 fill 점수를 계산
# - Worker는 "판단/추출"만 하고, 정답 비교/점수 계산은 API(results)에서 함
#
# 입력:
#   - image_path: 시험지 전체 이미지
#   - questions: [{question_id, roi: {x,y,w,h}, choices: ["A","B","C","D","E"]}, ...]
#   - threshold params
#
# 출력:
#   - answers: [OMRAnswerV1, ...]
# ------------------------------------------------------------

BBox = Tuple[int, int, int, int]


@dataclass(frozen=True)
class OMRConfigV1:
    # 이 값들은 v1 baseline. 운영하면서 조정 가능.
    # 0~1 fill 점수 (어두운 픽셀 비율 기반)
    blank_threshold: float = 0.08      # 모두 약하면 blank
    multi_threshold: float = 0.62      # 2개 이상이 이 이상이면 multi
    conf_gap_threshold: float = 0.08   # 1등-2등 차이가 이보다 작으면 ambiguous
    low_confidence_threshold: float = 0.70  # API 채점 정책과 맞추기 위해 동일 기본값


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _crop(gray: np.ndarray, bbox: BBox) -> np.ndarray:
    x, y, w, h = bbox
    x = max(0, int(x))
    y = max(0, int(y))
    w = max(1, int(w))
    h = max(1, int(h))
    return gray[y:y + h, x:x + w]


def _fill_score(roi_gray: np.ndarray) -> float:
    """
    ROI에서 '채워짐(fill)' 점수 계산 (0~1)
    - 매우 단순한 v1: 밝기 임계값으로 어두운 픽셀 비율
    """
    if roi_gray.size == 0:
        return 0.0

    # normalize
    blur = cv2.GaussianBlur(roi_gray, (5, 5), 0)

    # adaptive-ish: OTSU
    _, th = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # th: 채워진 부분이 255, 배경이 0 (INV)
    filled = float(np.sum(th > 0))
    total = float(th.size)
    if total <= 0:
        return 0.0

    score = filled / total
    # 0~1 clamp
    return float(max(0.0, min(1.0, score)))


def _split_choices_bbox(roi_bbox: BBox, n: int, axis: str = "x") -> List[BBox]:
    """
    ROI bbox를 n등분해서 choice별 bbox를 만든다.
    - axis="x": 가로로 n분할 (A B C D E가 가로배치인 경우)
    - axis="y": 세로로 n분할
    """
    x, y, w, h = roi_bbox
    boxes: List[BBox] = []
    if n <= 0:
        return boxes

    if axis == "y":
        step = h / float(n)
        for i in range(n):
            yy = y + int(round(i * step))
            hh = int(round(step))
            boxes.append((x, yy, w, max(1, hh)))
        return boxes

    # default x
    step = w / float(n)
    for i in range(n):
        xx = x + int(round(i * step))
        ww = int(round(step))
        boxes.append((xx, y, max(1, ww), h))
    return boxes


def detect_omr_answers_v1(
    *,
    image_path: str,
    questions: List[Dict[str, Any]],
    cfg: Optional[OMRConfigV1] = None,
) -> List[Dict[str, Any]]:
    """
    Worker entry: return list of dict payloads (each is OMRAnswerV1.to_dict()).
    """
    cfg = cfg or OMRConfigV1()

    img_bgr = cv2.imread(image_path)
    if img_bgr is None:
        # image load fail -> return error answers (best effort)
        out: List[Dict[str, Any]] = []
        for q in questions or []:
            qid = _safe_int(q.get("question_id"))
            out.append(
                OMRAnswerV1(
                    version="v1",
                    question_id=qid,
                    detected=[],
                    marking="blank",
                    confidence=0.0,
                    status="error",
                    raw={"error": "cannot read image"},
                ).to_dict()
            )
        return out

    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

    results: List[Dict[str, Any]] = []

    for q in questions or []:
        qid = _safe_int(q.get("question_id"))
        roi = q.get("roi") or {}
        roi_bbox: BBox = (
            _safe_int(roi.get("x")),
            _safe_int(roi.get("y")),
            _safe_int(roi.get("w"), 1),
            _safe_int(roi.get("h"), 1),
        )
        choices = q.get("choices") or ["A", "B", "C", "D", "E"]
        axis = (q.get("axis") or "x").lower()

        n = len(choices)
        choice_boxes = _split_choices_bbox(roi_bbox, n=n, axis=axis)

        marks = []
        for idx, cb in enumerate(choice_boxes):
            roi_choice = _crop(gray, cb)
            fill = _fill_score(roi_choice)
            marks.append({"choice": str(choices[idx]), "fill": float(fill)})

        # sort by fill desc
        marks_sorted = sorted(marks, key=lambda m: m["fill"], reverse=True)
        top = marks_sorted[0] if marks_sorted else {"choice": "", "fill": 0.0}
        second = marks_sorted[1] if len(marks_sorted) > 1 else {"choice": "", "fill": 0.0}

        top_fill = float(top.get("fill") or 0.0)
        second_fill = float(second.get("fill") or 0.0)

        # blank 판단
        if top_fill < cfg.blank_threshold:
            results.append(
                OMRAnswerV1(
                    version="v1",
                    question_id=qid,
                    detected=[],
                    marking="blank",
                    confidence=0.0,
                    status="blank",
                    raw={"marks": marks_sorted},
                ).to_dict()
            )
            continue

        # multi 판단 (v1: 2개 이상이 multi_threshold 이상이면 multi)
        high = [m for m in marks_sorted if float(m.get("fill") or 0.0) >= cfg.multi_threshold]
        if len(high) >= 2:
            detected = [str(m["choice"]) for m in high]
            results.append(
                OMRAnswerV1(
                    version="v1",
                    question_id=qid,
                    detected=detected,
                    marking="multi",
                    confidence=float(top_fill),
                    status="ambiguous",  # multi는 ambiguous로 처리 (v1)
                    raw={"marks": marks_sorted},
                ).to_dict()
            )
            continue

        # ambiguous 판단 (top-2 gap이 너무 작음)
        gap = top_fill - second_fill
        if gap < cfg.conf_gap_threshold:
            results.append(
                OMRAnswerV1(
                    version="v1",
                    question_id=qid,
                    detected=[str(top["choice"])],
                    marking="single",
                    confidence=float(top_fill),
                    status="ambiguous",
                    raw={"marks": marks_sorted, "gap": float(gap)},
                ).to_dict()
            )
            continue

        # ok / low_confidence
        status = "ok" if top_fill >= cfg.low_confidence_threshold else "low_confidence"

        results.append(
            OMRAnswerV1(
                version="v1",
                question_id=qid,
                detected=[str(top["choice"])],
                marking="single",
                confidence=float(top_fill),
                status=status,  # ok 또는 low_confidence
                raw={"marks": marks_sorted, "gap": float(gap)},
            ).to_dict()
        )

    return results
