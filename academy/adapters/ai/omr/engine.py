# apps/worker/ai_worker/ai/omr/engine.py
"""
OMR 객관식 답안 검출 엔진 v9

omr-sheet.html SSOT 레이아웃 기준.
meta_generator.py의 좌표를 사용하여 스캔 이미지에서 마킹된 버블을 감지한다.

v9 changes:
- Adaptive thresholding (replaces fixed threshold)
- Elliptical ROI masking (matches bubble shape)
- Multi-feature scoring: fill_ratio + darkness + uniformity
- Column-local alignment via anchors (v9 meta only)
- Backward compatible with v8 meta (skips local alignment)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

from academy.adapters.ai.omr.anchor_detector import detect_filled_anchor_square
from academy.adapters.ai.omr.meta_px import build_page_scale_from_meta, PageScale
from academy.adapters.ai.omr.types import OMRAnswerV1

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AnswerDetectConfig:
    """객관식 버블 감지 설정."""
    # ROI 확장 계수 (버블 반지름 x k)
    # 1.05: 인접 행/열 버블 침범 방지 + warp residual 1mm 허용 (1.2는 ~6mm 행 간격에서 침범)
    roi_expand_k: float = 1.05
    # blank 판단: 최고 score가 이 값 미만이면 blank
    blank_threshold: float = 0.08
    # ambiguous 판단: top-2 gap이 이 값 미만이면 ambiguous
    conf_gap_threshold: float = 0.06
    # adaptive threshold 사용 여부
    use_adaptive_threshold: bool = True
    # adaptive threshold block size (must be odd)
    adaptive_block_size: int = 15
    # adaptive threshold C constant (낮을수록 연한 마킹 감지 가능, 너무 낮으면 노이즈 증가)
    adaptive_c: int = 5
    # elliptical mask 사용 여부
    use_elliptical_mask: bool = True
    # multi-feature scoring 사용 여부
    use_multi_feature: bool = True
    # v10: CLAHE 전처리 (그림자/조명 불균일 보정)
    use_clahe: bool = True
    clahe_clip_limit: float = 2.0
    clahe_grid_size: int = 8
    # v10: 이미지 품질 기반 adaptive 파라미터 자동 조정
    use_quality_adaptive: bool = True
    # v10: border density 특성 (부분 마킹/오염 구분)
    use_border_density: bool = True


def detect_omr_answers_v7(
    *,
    image_bgr: np.ndarray,
    meta: Dict[str, Any],
    config: Optional[AnswerDetectConfig] = None,
) -> List[OMRAnswerV1]:
    """
    워프된 A4 이미지에서 객관식 답안을 감지한다.

    Args:
        image_bgr: 워프된 BGR 이미지 (전체 페이지 = 전체 이미지)
        meta: build_omr_meta() 결과
        config: 감지 설정

    Returns:
        문항별 OMRAnswerV1 리스트
    """
    if config is None:
        config = AnswerDetectConfig()

    scale = build_page_scale_from_meta(
        meta=meta,
        image_size_px=(image_bgr.shape[1], image_bgr.shape[0]),
    )

    gray_raw = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    gray = gray_raw.copy()

    # --- v10: CLAHE 전처리 (그림자/조명 불균일 보정) ---
    if config.use_clahe:
        clahe = cv2.createCLAHE(
            clipLimit=config.clahe_clip_limit,
            tileGridSize=(config.clahe_grid_size, config.clahe_grid_size),
        )
        gray = clahe.apply(gray)

    # --- v10: 이미지 품질 기반 파라미터 자동 조정 ---
    block_size = config.adaptive_block_size
    adaptive_c = config.adaptive_c
    if config.use_quality_adaptive:
        blur_metric = cv2.Laplacian(gray, cv2.CV_64F).var()
        if blur_metric < 100:
            # 흐릿한 이미지: 블록 크기 키우고 C 낮춤
            block_size = max(block_size, 21)
            adaptive_c = max(2, adaptive_c - 2)
            logger.info("OMR quality: blurry (laplacian_var=%.1f), block=%d C=%d", blur_metric, block_size, adaptive_c)
        elif blur_metric > 2000:
            # 매우 선명: 기본값 유지
            pass

    # --- Binarization ---
    if config.use_adaptive_threshold:
        blur = cv2.GaussianBlur(gray, (5, 5), 0)
        binary = cv2.adaptiveThreshold(
            blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            blockSize=block_size,
            C=adaptive_c,
        )
    else:
        # Legacy fixed threshold fallback
        _, binary = cv2.threshold(gray, 140, 255, cv2.THRESH_BINARY_INV)

    # --- Column-local alignment (v9~v14, displacement-gated) ---
    # v15: 컬럼 앵커 원 제거됨 — 코너 마커 homography만으로 정렬. col_transforms는 빈 dict.
    col_transforms: Dict[int, np.ndarray] = {}
    meta_version = meta.get("version", "v8")
    if meta_version in ("v9", "v10", "v11", "v12", "v13", "v14", "v15") and meta.get("columns"):
        raw_transforms = _compute_column_transforms(
            gray=gray, scale=scale, columns_meta=meta["columns"],
        )
        # marker/rotation 이후 잔여 보정. rotation_only fallback에서는 스캔 여백 차이로
        # 8mm 안팎의 평행 이동이 남을 수 있어 실제 컬럼 앵커 기준으로 10mm까지 허용한다.
        _MAX_COL_DISPLACEMENT_PX = 10.0 * scale.sx  # 10mm를 픽셀로 환산
        for ci, M in raw_transforms.items():
            # displacement = M @ [0,0,1] 의 translation 성분
            dx, dy = abs(M[0, 2]), abs(M[1, 2])
            if dx <= _MAX_COL_DISPLACEMENT_PX and dy <= _MAX_COL_DISPLACEMENT_PX:
                col_transforms[ci] = M
            else:
                logger.debug(
                    "Column %d transform rejected: dx=%.1f dy=%.1f exceeds %.1fpx",
                    ci, dx, dy, _MAX_COL_DISPLACEMENT_PX,
                )

    results: List[OMRAnswerV1] = []

    for q in meta.get("questions", []):
        q_num = int(q.get("question_number", 0))
        if q.get("type") == "numeric_short_answer":
            try:
                results.append(_detect_numeric_short_question(
                    gray=gray,
                    gray_raw=gray_raw,
                    binary=binary,
                    scale=scale,
                    question=q,
                    config=config,
                    img_shape=image_bgr.shape,
                    meta_version=meta_version,
                ))
            except Exception:
                logger.exception("OMR numeric short-answer detect error q=%d", q_num)
                results.append(OMRAnswerV1(
                    version=meta_version,
                    question_id=q_num,
                    detected=[],
                    marking="blank",
                    confidence=0.0,
                    status="error",
                ))
            continue
        choices = q.get("choices", [])
        if not choices:
            continue

        # Determine which column this question belongs to (for local alignment)
        col_idx = int(q.get("column", -1))

        try:
            answer = _detect_single_question(
                gray=gray,
                gray_raw=gray_raw,
                binary=binary,
                scale=scale,
                q_num=q_num,
                choices=choices,
                config=config,
                img_shape=image_bgr.shape,
                col_transform=col_transforms.get(col_idx),
                meta_version=meta_version,
            )
            results.append(answer)
        except Exception:
            logger.exception("OMR detect error q=%d", q_num)
            results.append(OMRAnswerV1(
                version=meta_version,
                question_id=q_num,
                detected=[],
                marking="blank",
                confidence=0.0,
                status="error",
            ))

    return results


def _detect_numeric_short_question(
    *,
    gray: np.ndarray,
    gray_raw: np.ndarray,
    binary: np.ndarray,
    scale: PageScale,
    question: Dict[str, Any],
    config: AnswerDetectConfig,
    img_shape: Tuple[int, ...],
    meta_version: str,
) -> OMRAnswerV1:
    question_number = int(question.get("question_number", 0))
    digit_results: List[OMRAnswerV1] = []
    for group in question.get("digit_groups", []):
        choices = [
            {
                "label": str(bubble.get("value", "")),
                "center": bubble.get("center", {}),
                "radius_x": bubble.get("radius_x", 1.8),
                "radius_y": bubble.get("radius_y", 2.6),
            }
            for bubble in group.get("bubbles", [])
        ]
        digit_results.append(_detect_single_question(
            gray=gray,
            gray_raw=gray_raw,
            binary=binary,
            scale=scale,
            q_num=question_number,
            choices=choices,
            config=config,
            img_shape=img_shape,
            col_transform=None,
            meta_version=meta_version,
        ))

    raw_digits = []
    bubble_rects = []
    for index, result in enumerate(digit_results):
        raw_digits.append({
            "digit_index": index,
            "detected": result.detected,
            "status": result.status,
            "confidence": result.confidence,
            "raw": result.raw,
        })
        if result.raw and isinstance(result.raw.get("bubble_rects"), list):
            for rect in result.raw["bubble_rects"]:
                bubble_rects.append({**rect, "digit_index": index})

    if not digit_results or all(result.status == "blank" for result in digit_results):
        return OMRAnswerV1(
            version=meta_version,
            question_id=question_number,
            detected=[],
            marking="blank",
            confidence=0.0,
            status="blank",
            raw={"digits": raw_digits, "bubble_rects": bubble_rects},
        )

    first_marked = next(
        (index for index, result in enumerate(digit_results) if result.status != "blank"),
        len(digit_results),
    )
    invalid_group = any(
        result.status != "ok" or len(result.detected) != 1
        for result in digit_results[first_marked:]
    )
    if invalid_group:
        status = "error" if any(result.status == "error" for result in digit_results) else "ambiguous"
        return OMRAnswerV1(
            version=meta_version,
            question_id=question_number,
            detected=[],
            marking="multi",
            confidence=0.0,
            status=status,
            raw={"digits": raw_digits, "bubble_rects": bubble_rects},
        )

    digits = "".join(result.detected[0] for result in digit_results[first_marked:])
    answer = str(int(digits))
    confidence = min(result.confidence for result in digit_results[first_marked:])
    return OMRAnswerV1(
        version=meta_version,
        question_id=question_number,
        detected=[answer],
        marking="single",
        confidence=round(float(confidence), 4),
        status="ok",
        raw={"digits": raw_digits, "bubble_rects": bubble_rects},
    )


def _compute_column_transforms(
    *,
    gray: np.ndarray,
    scale: PageScale,
    columns_meta: List[Dict[str, Any]],
) -> Dict[int, np.ndarray]:
    """
    v9 column-local alignment: detect column anchor squares and compute
    per-column affine correction matrix.

    Returns dict mapping column_index -> 2x3 affine transform matrix.
    If anchor detection fails for a column, that column is omitted (no correction).
    """
    transforms: Dict[int, np.ndarray] = {}

    for col_meta in columns_meta:
        col_idx = int(col_meta.get("column_index", -1))
        anchors = col_meta.get("anchors", {})
        if not anchors:
            continue

        # Expect anchors with "top" and "bottom" each having {x, y} in mm
        top_anchor = anchors.get("top")
        bottom_anchor = anchors.get("bottom")
        if not top_anchor or not bottom_anchor:
            continue

        try:
            # Expected anchor positions (from meta, mm -> px)
            top_c = top_anchor.get("center", top_anchor)
            bot_c = bottom_anchor.get("center", bottom_anchor)
            top_exp_x, top_exp_y = scale.mm_to_px_point(
                float(top_c["x"]), float(top_c["y"])
            )
            bot_exp_x, bot_exp_y = scale.mm_to_px_point(
                float(bot_c["x"]), float(bot_c["y"])
            )

            # Detect actual anchor positions in the image
            top_det = _detect_anchor_square(gray, top_exp_x, top_exp_y, scale)
            bot_det = _detect_anchor_square(gray, bot_exp_x, bot_exp_y, scale)

            if top_det is None or bot_det is None:
                continue

            # Build affine from expected -> detected shift
            # Use 3-point affine: top anchor, bottom anchor, and a midpoint offset
            src_pts = np.array([
                [top_exp_x, top_exp_y],
                [bot_exp_x, bot_exp_y],
                [top_exp_x + 100, top_exp_y],  # synthetic third point
            ], dtype=np.float32)

            dx_top = top_det[0] - top_exp_x
            dy_top = top_det[1] - top_exp_y
            dx_bot = bot_det[0] - bot_exp_x
            dy_bot = bot_det[1] - bot_exp_y

            dst_pts = np.array([
                [top_exp_x + dx_top, top_exp_y + dy_top],
                [bot_exp_x + dx_bot, bot_exp_y + dy_bot],
                [top_exp_x + 100 + dx_top, top_exp_y + dy_top],
            ], dtype=np.float32)

            M = cv2.getAffineTransform(src_pts, dst_pts)
            transforms[col_idx] = M
        except Exception:
            logger.debug("Column %d anchor detection failed", col_idx)
            continue

    return transforms


def _detect_anchor_square(
    gray: np.ndarray,
    expected_x: int,
    expected_y: int,
    scale: PageScale,
    search_radius_mm: float = 12.0,
) -> Optional[Tuple[int, int]]:
    return detect_filled_anchor_square(
        gray=gray,
        expected_x=expected_x,
        expected_y=expected_y,
        scale=scale,
        search_radius_mm=search_radius_mm,
    )


def _apply_col_transform(
    cx_px: int, cy_px: int, col_transform: Optional[np.ndarray],
) -> Tuple[int, int]:
    """Apply column affine transform to adjust bubble center coordinates."""
    if col_transform is None:
        return cx_px, cy_px
    pt = np.array([cx_px, cy_px, 1.0], dtype=np.float64)
    result = col_transform @ pt
    return int(round(result[0])), int(round(result[1]))


def _compute_bubble_score(
    *,
    gray: np.ndarray,
    gray_raw: np.ndarray,
    binary: np.ndarray,
    cx_px: int,
    cy_px: int,
    rx_px: int,
    ry_px: int,
    img_h: int,
    img_w: int,
    config: AnswerDetectConfig,
) -> Tuple[float, Dict[str, float]]:
    """
    Compute multi-feature score for a single bubble.

    Returns:
        (score, details_dict) where score is 0.0~1.0 composite,
        details_dict has fill_ratio, darkness, uniformity.
    """
    x1 = max(0, cx_px - rx_px)
    y1 = max(0, cy_px - ry_px)
    x2 = min(img_w, cx_px + rx_px)
    y2 = min(img_h, cy_px + ry_px)

    roi_h = y2 - y1
    roi_w = x2 - x1

    if roi_h <= 0 or roi_w <= 0:
        return 0.0, {"fill_ratio": 0.0, "darkness": 0.0, "uniformity": 0.0}

    binary_roi = binary[y1:y2, x1:x2]
    gray_roi = gray[y1:y2, x1:x2]

    # --- Elliptical mask (fill_ratio + raw_darkness 공용) ---
    mask: Optional[np.ndarray] = None
    if config.use_elliptical_mask:
        mask = np.zeros((roi_h, roi_w), dtype=np.uint8)
        cv2.ellipse(
            mask,
            (roi_w // 2, roi_h // 2),
            (roi_w // 2, roi_h // 2),
            0, 0, 360, 255, -1,
        )

    # --- Fill ratio ---
    if mask is not None:
        filled_pixels = np.count_nonzero(cv2.bitwise_and(binary_roi, mask))
        total_pixels = np.count_nonzero(mask)
        fill_ratio = float(filled_pixels) / max(1, total_pixels)
    else:
        fill_ratio = float(np.count_nonzero(binary_roi)) / max(1, binary_roi.size)

    if not config.use_multi_feature:
        return fill_ratio, {"fill_ratio": fill_ratio, "darkness": 0.0, "uniformity": 0.0, "border_density": 0.0, "raw_darkness": 0.0}

    # --- Darkness & Uniformity (center 60% area) ---
    margin_x = int(roi_w * 0.2)
    margin_y = int(roi_h * 0.2)
    center_roi = gray_roi[margin_y:roi_h - margin_y, margin_x:roi_w - margin_x]

    if center_roi.size == 0:
        center_roi = gray_roi  # fallback to full ROI

    mean_intensity = float(np.mean(center_roi))
    std_intensity = float(np.std(center_roi))

    darkness = 1.0 - (mean_intensity / 255.0)
    uniformity = 1.0 - (std_intensity / 128.0)
    uniformity = max(0.0, min(1.0, uniformity))

    # --- v11: Raw darkness (CLAHE/adaptive 영향 없는 원본 기반) ---
    # CLAHE가 지역적으로 명암을 평탄화하면 darkness가 왜곡됨.
    # 원본 gray의 평균 밝기로 마킹 여부를 가장 안정적으로 판별.
    raw_roi = gray_raw[y1:y2, x1:x2]
    if mask is not None and raw_roi.shape == mask.shape:
        raw_masked = cv2.bitwise_and(raw_roi, mask)
        raw_pixels = raw_masked[mask > 0]
    else:
        raw_pixels = raw_roi.flatten()
    raw_mean = float(np.mean(raw_pixels)) if raw_pixels.size > 0 else 255.0
    raw_darkness = 1.0 - (raw_mean / 255.0)

    # --- v10: Border density (부분 마킹/오염 구분) ---
    border_density = 0.0
    if config.use_border_density and roi_h > 4 and roi_w > 4:
        center_binary = binary_roi[margin_y:roi_h - margin_y, margin_x:roi_w - margin_x]
        center_fill = float(np.count_nonzero(center_binary)) / max(1, center_binary.size)
        border_density = min(1.0, center_fill / max(0.01, fill_ratio))

    # Composite score: raw_darkness를 primary feature로 사용
    # raw_darkness는 CLAHE/adaptive 영향을 받지 않아 페이지 전체에서 일관적.
    if config.use_border_density:
        score = 0.15 * fill_ratio + 0.15 * darkness + 0.10 * uniformity + 0.10 * border_density + 0.50 * raw_darkness
    else:
        score = 0.20 * fill_ratio + 0.15 * darkness + 0.15 * uniformity + 0.50 * raw_darkness

    return score, {
        "fill_ratio": round(fill_ratio, 4),
        "darkness": round(darkness, 4),
        "uniformity": round(uniformity, 4),
        "border_density": round(border_density, 4),
        "raw_darkness": round(raw_darkness, 4),
    }


def _detect_single_question(
    *,
    gray: np.ndarray,
    gray_raw: np.ndarray,
    binary: np.ndarray,
    scale: PageScale,
    q_num: int,
    choices: List[Dict[str, Any]],
    config: AnswerDetectConfig,
    img_shape: Tuple[int, ...],
    col_transform: Optional[np.ndarray] = None,
    meta_version: str = "v8",
) -> OMRAnswerV1:
    """단일 문항의 버블 multi-feature scoring 및 판정."""
    img_h, img_w = img_shape[:2]
    fills: List[Tuple[str, float, Dict[str, float]]] = []
    choice_order = {str(ch.get("label", "")): i for i, ch in enumerate(choices)}
    # v10.1: 각 버블의 픽셀 좌표 기록 (검토 UI의 BBox overlay용). 판정 로직에는 영향 없음.
    bubble_rects: List[Dict[str, int]] = []

    for ch in choices:
        label = str(ch.get("label", ""))
        center = ch.get("center", {})
        cx_mm = float(center.get("x", 0))
        cy_mm = float(center.get("y", 0))
        rx_mm = float(ch.get("radius_x", 1.8))
        ry_mm = float(ch.get("radius_y", 2.6))

        cx_px, cy_px = scale.mm_to_px_point(cx_mm, cy_mm)

        # Apply column-local affine correction (v9 only)
        if col_transform is not None:
            cx_px, cy_px = _apply_col_transform(cx_px, cy_px, col_transform)
            cx_px = max(0, min(img_w - 1, cx_px))
            cy_px = max(0, min(img_h - 1, cy_px))

        rx_px = max(1, int(round(rx_mm * config.roi_expand_k * scale.sx)))
        ry_px = max(1, int(round(ry_mm * config.roi_expand_k * scale.sy)))

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
        fills.append((label, score, details))
        # bbox = 원의 외접 사각형 (x, y, w, h)
        bubble_rects.append({
            "label": label,
            "x": int(max(0, cx_px - rx_px)),
            "y": int(max(0, cy_px - ry_px)),
            "w": int(min(img_w, cx_px + rx_px) - max(0, cx_px - rx_px)),
            "h": int(min(img_h, cy_px + ry_px) - max(0, cy_px - ry_px)),
        })

    if not fills:
        return OMRAnswerV1(
            version=meta_version, question_id=q_num,
            detected=[], marking="blank",
            confidence=0.0, status="error",
        )

    # 정렬: score 높은 순
    fills.sort(key=lambda x: x[1], reverse=True)
    top_label, top_score, top_details = fills[0]
    second_score = fills[1][1] if len(fills) > 1 else 0.0
    gap = top_score - second_score

    # 문항 전체 rect = 모든 버블의 bounding box (검토 UI 좌표)
    if bubble_rects:
        min_x = min(br["x"] for br in bubble_rects)
        min_y = min(br["y"] for br in bubble_rects)
        max_x = max(br["x"] + br["w"] for br in bubble_rects)
        max_y = max(br["y"] + br["h"] for br in bubble_rects)
        question_rect: Optional[Dict[str, int]] = {
            "x": int(min_x),
            "y": int(min_y),
            "w": int(max_x - min_x),
            "h": int(max_y - min_y),
        }
    else:
        question_rect = None

    raw_data = {
        "fills": {l: round(s, 4) for l, s, _ in fills},
        "details": {l: d for l, _, d in fills},
        "bubble_rects": bubble_rects,
        "rect": question_rect,
    }

    # ── 판정 (v15: 강건한 noise floor 추정) ──
    # 노이즈는 "마킹 안 된 버블들"의 점수 분포여야 한다. 이중마킹/삼중마킹이 있으면
    # 전체 IQR이 폭증해 blank threshold가 비현실적으로 커진다 → bottom-half에서만
    # noise를 추정해 bimodal에 강건하게.
    # v14까지 있던 `top_score < 0.35` 절대 임계값은 폐기 — 저농도 인쇄/연필 마킹에서
    # 정상 마크가 0.35 미만이면 항상 blank로 오판하던 false negative 차단.
    scores_arr = np.array([s for _, s, _ in fills])
    sorted_asc = np.sort(scores_arr)
    half = max(2, len(sorted_asc) // 2)  # 5지선다 → 하위 2개를 noise로
    noise_pool = sorted_asc[:half]
    noise_median = float(np.median(noise_pool))
    if len(noise_pool) >= 2:
        n_q1 = float(np.percentile(noise_pool, 25))
        n_q3 = float(np.percentile(noise_pool, 75))
        noise_std = (n_q3 - n_q1) / 1.35
    else:
        noise_std = 0.0
    median_score = noise_median  # noise 바닥을 비교 baseline으로
    relative_top = top_score - median_score

    # Blank: 최고 점수가 noise floor 대비 유의미하게 높지 않으면 blank
    _REL_BLANK_TH = max(0.04, 2.0 * noise_std)  # 동적 threshold
    is_blank = relative_top < _REL_BLANK_TH

    if is_blank:
        return OMRAnswerV1(
            version=meta_version, question_id=q_num,
            detected=[], marking="blank",
            confidence=0.0, status="blank",
            raw=raw_data,
        )

    # 복수 마킹 가능성 — 상대적 noise floor 기준.
    # 실제 학생 마킹은 두 버블 농도가 다르면 top-2 gap이 커질 수 있다. gap이 작을 때만
    # multi를 검사하면 "진한 3번 + 연한 4번" 같은 명확한 이중마킹을 single로 접는다.
    noise_floor = median_score + _REL_BLANK_TH
    marked_pairs = [(l, s) for l, s, _ in fills if s >= noise_floor]
    marked_labels = {l for l, _ in marked_pairs}
    marked = sorted(
        marked_labels,
        key=lambda label: choice_order.get(str(label), len(choice_order)),
    )

    if len(marked_pairs) > 1:
        weakest_mark = min(s for _, s in marked_pairs)
        unmarked_scores = [s for l, s, _ in fills if l not in marked_labels]
        next_score = max(unmarked_scores) if unmarked_scores else median_score
        selected_separation = weakest_mark - next_score
        strong_multi = (
            top_score >= max(0.28, median_score + 0.12)
            and weakest_mark >= median_score + 0.10
            and selected_separation >= max(0.08, 3.0 * noise_std)
        )
        if strong_multi:
            raw_data["multi_decision"] = {
                "noise_floor": round(noise_floor, 4),
                "weakest_mark": round(weakest_mark, 4),
                "next_score": round(next_score, 4),
                "selected_separation": round(selected_separation, 4),
                "decision": "clear_multi",
            }
            return OMRAnswerV1(
                version=meta_version,
                question_id=q_num,
                detected=marked,
                marking="multi",
                confidence=round(min(1.0, max(0.0, selected_separation / 0.3)), 4),
                status="ok",
                raw=raw_data,
            )

    if gap < config.conf_gap_threshold:

        raw_data["multi_decision"] = {
            "noise_floor": round(noise_floor, 4),
            "decision": "ambiguous_multi" if len(marked) > 1 else "ambiguous_single",
        }
        return OMRAnswerV1(
            version=meta_version, question_id=q_num,
            detected=marked,
            marking="multi" if len(marked) > 1 else "single",
            confidence=round(gap, 4),
            status="ambiguous",
            raw=raw_data,
        )

    confidence = min(1.0, gap / 0.3)  # gap 0.3 이상이면 confidence 1.0

    return OMRAnswerV1(
        version=meta_version, question_id=q_num,
        detected=[top_label],
        marking="single",
        confidence=round(confidence, 4),
        status="ok",
        raw=raw_data,
    )
