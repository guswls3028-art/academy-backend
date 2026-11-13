"""Read teacher-marked essay scores from the score strip of each essay row.

The strip (tens 0~4, ones 0~9, tenths .0~.9) is printed only on sheets whose contract
enabled it; ``meta["essay_score_marks"]`` carries its coordinates
(``meta_generator.build_essay_score_marks``). The two solid anchors at the
ends of each strip must be present: a sheet printed without the strip has
handwriting there, never two 2mm squares at both expected points.

Every bubble of one strip shares one noise floor. A teacher marks at most
three bubbles of twenty-five, so the lower half of the scores is paper/print
only, even for the five-bubble tens group. Results are proposals for the
grading grid; the backend never writes them as canonical scores.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np  # type: ignore

from academy.adapters.ai.omr.anchor_detector import detect_filled_anchor_square
from academy.adapters.ai.omr.engine import (
    AnswerDetectConfig,
    PreparedOmrImages,
    _compute_bubble_score,
    prepare_omr_images,
)
from academy.adapters.ai.omr.meta_px import PageScale

logger = logging.getLogger(__name__)

ESSAY_SCORE_READ_VERSION = "essay_score_marks.v1"
STATUS_OK = "ok"
STATUS_BLANK = "blank"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_NO_SCORE_AREA = "no_score_area"
STATUS_ERROR = "error"


@dataclass(frozen=True)
class EssayScoreReadConfig:
    # Anchors must sit within this distance of their printed position.
    anchor_search_mm: float = 3.5
    # Distance between the detected anchors may differ this much from print.
    anchor_span_tolerance_mm: float = 2.0
    # Minimum ink evidence over the strip's paper/print floor.
    min_relative_mark: float = 0.04
    # A second bubble this close to the top bubble of a group is ambiguous.
    group_gap: float = 0.06


def detect_essay_score_marks(
    *,
    image_bgr: np.ndarray,
    meta: Dict[str, Any],
    config: Optional[AnswerDetectConfig] = None,
    read_config: Optional[EssayScoreReadConfig] = None,
    prepared_images: PreparedOmrImages | None = None,
) -> List[Dict[str, Any]]:
    """Read score strips from the same image, metadata and config as any prepared inputs."""
    marks = meta.get("essay_score_marks")
    if not isinstance(marks, list) or not marks:
        return []
    config = config or AnswerDetectConfig()
    read_config = read_config or EssayScoreReadConfig()
    scale, gray_raw, gray, binary = prepared_images or prepare_omr_images(
        image_bgr=image_bgr, meta=meta, config=config,
    )
    results: List[Dict[str, Any]] = []
    for mark in marks:
        number = int(mark.get("question_number") or 0)
        try:
            results.append(_read_strip(
                mark=mark,
                scale=scale,
                gray_raw=gray_raw,
                gray=gray,
                binary=binary,
                config=config,
                read_config=read_config,
            ))
        except Exception:
            logger.exception("OMR essay score read error q=%d", number)
            results.append(_result(number, STATUS_ERROR))
    return results


def _result(number: int, status: str, **extra: Any) -> Dict[str, Any]:
    return {
        "version": ESSAY_SCORE_READ_VERSION,
        "question_number": number,
        "status": status,
        "score": None,
        "confidence": 0.0,
        **extra,
    }


def _anchor_offsets(
    *, mark: Dict[str, Any], scale: PageScale, gray: np.ndarray, read_config: EssayScoreReadConfig,
) -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
    """Return ((x_expected, dx, dy) left, (x_expected, dx, dy) right) in px."""
    found = []
    for side in ("left", "right"):
        center = mark["anchors"][side]["center"]
        ex, ey = scale.mm_to_px_point(float(center["x"]), float(center["y"]))
        detected = detect_filled_anchor_square(
            gray=gray,
            expected_x=ex,
            expected_y=ey,
            scale=scale,
            search_radius_mm=read_config.anchor_search_mm,
        )
        if detected is None:
            return None
        found.append((float(ex), float(ey), float(detected[0]), float(detected[1])))
    (lex, ley, ldx, ldy), (rex, rey, rdx, rdy) = found
    expected_span = ((rex - lex) ** 2 + (rey - ley) ** 2) ** 0.5
    detected_span = ((rdx - ldx) ** 2 + (rdy - ldy) ** 2) ** 0.5
    if abs(detected_span - expected_span) > read_config.anchor_span_tolerance_mm * scale.sx:
        return None
    return (lex, ldx - lex, ldy - ley), (rex, rdx - rex, rdy - rey)


def _read_strip(
    *,
    mark: Dict[str, Any],
    scale: PageScale,
    gray_raw: np.ndarray,
    gray: np.ndarray,
    binary: np.ndarray,
    config: AnswerDetectConfig,
    read_config: EssayScoreReadConfig,
) -> Dict[str, Any]:
    number = int(mark.get("question_number") or 0)
    offsets = _anchor_offsets(mark=mark, scale=scale, gray=gray, read_config=read_config)
    if offsets is None:
        return _result(number, STATUS_NO_SCORE_AREA)
    (left_x, left_dx, left_dy), (right_x, right_dx, right_dy) = offsets
    img_h, img_w = gray.shape[:2]

    def corrected(cx_mm: float, cy_mm: float) -> Tuple[int, int]:
        x_px, y_px = scale.mm_to_px_point(cx_mm, cy_mm)
        t = 0.0 if right_x == left_x else (x_px - left_x) / (right_x - left_x)
        t = max(0.0, min(1.0, t))
        x_px = int(round(x_px + left_dx + (right_dx - left_dx) * t))
        y_px = int(round(y_px + left_dy + (right_dy - left_dy) * t))
        return max(0, min(img_w - 1, x_px)), max(0, min(img_h - 1, y_px))

    scored: List[Tuple[str, str, float, Dict[str, float], Dict[str, int]]] = []
    for group in mark.get("groups") or []:
        place = str(group.get("place") or "")
        for bubble in group.get("bubbles") or []:
            cx_px, cy_px = corrected(float(bubble["center"]["x"]), float(bubble["center"]["y"]))
            rx_px = max(1, int(round(float(bubble["radius_x"]) * config.roi_expand_k * scale.sx)))
            ry_px = max(1, int(round(float(bubble["radius_y"]) * config.roi_expand_k * scale.sy)))
            score, details = _compute_bubble_score(
                gray=gray,
                gray_raw=gray_raw,
                binary=binary,
                cx_px=cx_px,
                cy_px=cy_px,
                rx_px=rx_px,
                ry_px=ry_px,
                img_h=img_h,
                img_w=img_w,
                config=config,
            )
            rect = {
                "x": int(max(0, cx_px - rx_px)),
                "y": int(max(0, cy_px - ry_px)),
                "w": int(2 * rx_px),
                "h": int(2 * ry_px),
            }
            scored.append((place, str(bubble.get("value")), float(score), details, rect))
    if not scored:
        return _result(number, STATUS_ERROR)

    scores = np.array([item[2] for item in scored])
    noise_pool = np.sort(scores)[: max(2, len(scores) // 2)]
    noise_median = float(np.median(noise_pool))
    noise_std = float(np.percentile(noise_pool, 75) - np.percentile(noise_pool, 25)) / 1.35
    unfilled = sorted(
        item[2] for item in scored if item[3].get("interior_contrast", 1.0) < 0.03
    )
    print_baseline = float(np.median(unfilled[len(unfilled) // 2:])) if unfilled else noise_median
    floor = max(noise_median, print_baseline) + max(read_config.min_relative_mark, 2.0 * noise_std)

    groups: Dict[str, Dict[str, Any]] = {}
    for place in ("tens", "ones", "tenths"):
        members = sorted(
            (item for item in scored if item[0] == place),
            key=lambda item: item[2],
            reverse=True,
        )
        marked = [item for item in members if item[2] >= floor]
        if not marked:
            groups[place] = {"status": STATUS_BLANK, "value": None, "confidence": 0.0}
            continue
        second = members[1][2] if len(members) > 1 else max(noise_median, print_baseline)
        gap = marked[0][2] - second
        if len(marked) > 1 or (len(members) > 1 and gap < read_config.group_gap):
            groups[place] = {
                "status": STATUS_AMBIGUOUS,
                "value": None,
                "marked": [item[1] for item in marked],
                "confidence": 0.0,
            }
            continue
        groups[place] = {
            "status": STATUS_OK,
            "value": marked[0][1],
            "confidence": round(min(1.0, max(0.0, gap) / 0.3), 4),
        }

    raw = {
        "floor": round(floor, 4),
        "fills": {f"{item[0]}:{item[1]}": round(item[2], 4) for item in scored},
        "bubble_rects": [{"place": item[0], "label": item[1], **item[4]} for item in scored],
    }
    if all(group["status"] == STATUS_BLANK for group in groups.values()):
        return _result(number, STATUS_BLANK, groups=groups, raw=raw)
    if any(group["status"] == STATUS_AMBIGUOUS for group in groups.values()):
        return _result(number, STATUS_AMBIGUOUS, groups=groups, raw=raw)
    if groups["ones"]["status"] != STATUS_OK:
        # Every mark needs the ones digit (0.3점 = 일 0 + 소수 3). Tens/tenths alone are unclear.
        return _result(number, STATUS_AMBIGUOUS, groups=groups, raw=raw)

    tens = int(groups["tens"]["value"]) if groups["tens"]["status"] == STATUS_OK else 0
    ones = int(groups["ones"]["value"])
    tenths = int(groups["tenths"]["value"]) if groups["tenths"]["status"] == STATUS_OK else 0
    confidence = min(
        group["confidence"] for group in groups.values() if group["status"] == STATUS_OK
    )
    return {
        **_result(number, STATUS_OK, groups=groups, raw=raw),
        "score": round(tens * 10 + ones + tenths / 10, 1),
        "confidence": confidence,
    }
