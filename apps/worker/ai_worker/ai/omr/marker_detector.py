# apps/worker/ai_worker/ai/omr/marker_detector.py
"""
OMR v9 비대칭 코너 마커 검출기.

4코너에 서로 다른 모양의 마커(square, L, T, plus)를 검출하여
페이지 방향과 회전을 안정적으로 판별한다.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# A4 landscape dimensions in mm (used for DPI estimation)
_A4_W_MM = 297.0
_A4_H_MM = 210.0

# Marker type canonical names
SQUARE = "square"
L_SHAPE = "l_shape"
T_SHAPE = "t_shape"
PLUS = "plus"
UNKNOWN = "unknown"

# Default v9 marker-corner mapping (orientation 0°):
#   TL=square, TR=l_shape, BL=t_shape, BR=plus
#   (matches meta_generator._build_marker_meta)
# Rotated by 90° CW the mapping shifts cyclically.
_DEFAULT_CORNER_MAP: Dict[int, Dict[str, str]] = {
    0: {"TL": SQUARE, "TR": L_SHAPE, "BL": T_SHAPE, "BR": PLUS},
    90: {"TL": PLUS, "TR": SQUARE, "BL": L_SHAPE, "BR": T_SHAPE},
    180: {"TL": T_SHAPE, "TR": PLUS, "BL": SQUARE, "BR": L_SHAPE},
    270: {"TL": L_SHAPE, "TR": T_SHAPE, "BL": PLUS, "BR": SQUARE},
}

_ALL_MARKER_TYPES = frozenset({SQUARE, L_SHAPE, T_SHAPE, PLUS})

# Corner regions: (x_range_frac, y_range_frac) — outer 15% band
_CORNER_REGIONS: Dict[str, Tuple[Tuple[float, float], Tuple[float, float]]] = {
    "TL": ((0.0, 0.15), (0.0, 0.15)),
    "TR": ((0.85, 1.0), (0.0, 0.15)),
    "BR": ((0.85, 1.0), (0.85, 1.0)),
    "BL": ((0.0, 0.15), (0.85, 1.0)),
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DetectedMarker:
    """A single detected corner marker."""
    corner: str  # "TL", "TR", "BL", "BR"
    marker_type: str  # "square", "l_shape", "t_shape", "plus"
    center_px: Tuple[int, int]
    contour: np.ndarray
    confidence: float


@dataclass
class MarkerDetectionResult:
    """Result of marker detection across all 4 corners."""
    markers: Dict[str, DetectedMarker] = field(default_factory=dict)
    orientation: int = 0  # 0, 90, 180, 270
    success: bool = False
    method: str = "fallback"  # "marker" or "fallback"


# ---------------------------------------------------------------------------
# Shape classification
# ---------------------------------------------------------------------------

def _count_significant_defects(
    contour: np.ndarray,
    hull_indices: np.ndarray,
    char_size: float,
    depth_ratio: float = 0.10,
) -> Tuple[int, float]:
    """Count convex hull defects deeper than depth_ratio * char_size."""
    if hull_indices is None or len(hull_indices) <= 3 or len(contour) <= 3:
        return 0, 0.0
    try:
        defects = cv2.convexityDefects(contour, hull_indices)
    except cv2.error:
        return 0, 0.0
    if defects is None:
        return 0, 0.0
    threshold = char_size * depth_ratio
    count = 0
    max_depth = 0.0
    for d in defects:
        depth = d[0][3] / 256.0
        max_depth = max(max_depth, depth)
        if depth > threshold:
            count += 1
    return count, max_depth


def classify_blob(contour: np.ndarray) -> str:
    """
    Classify a contour as one of the v9 marker shapes.

    Uses solidity, aspect ratio, and convex hull defect analysis
    to distinguish between: square, l_shape/t_shape (junction), plus, unknown.

    Note: L-shape and T-shape are topologically identical for thin-stroke markers
    (both are two perpendicular bars meeting at an end). This function classifies
    both as T_SHAPE. The caller must use spatial assignment to disambiguate them
    based on expected positions from the meta template.

    Args:
        contour: OpenCV contour (N, 1, 2) array.

    Returns:
        One of "square", "l_shape", "t_shape", "plus", "unknown".
    """
    area = cv2.contourArea(contour)
    if area < 1:
        return UNKNOWN

    hull = cv2.convexHull(contour)
    hull_area = cv2.contourArea(hull)
    solidity = area / hull_area if hull_area > 0 else 0.0

    x, y, w, h = cv2.boundingRect(contour)
    aspect = w / h if h > 0 else 0.0

    hull_indices = cv2.convexHull(contour, returnPoints=False)
    char_size = math.sqrt(area) if area > 0 else 1.0

    # Count significant defects at a loose threshold
    sig_loose, max_depth = _count_significant_defects(
        contour, hull_indices, char_size, 0.10,
    )

    # --- Classification rules ---

    # SQUARE: high solidity, roughly square aspect, no significant concavities
    if solidity > 0.85 and 0.70 <= aspect <= 1.42:
        return SQUARE

    # PLUS: low solidity, roughly symmetric, 4+ concavities
    if solidity <= 0.70 and sig_loose >= 4:
        if 0.60 <= aspect <= 1.65:
            return PLUS

    # JUNCTION (L or T): low-to-moderate solidity, 1-3 concavities
    # Both L and T shapes with thin strokes show 1-2 significant defects.
    # They are classified as the same type here; spatial assignment resolves them.
    if solidity <= 0.75 and 1 <= sig_loose <= 3:
        return T_SHAPE  # canonical "junction" type

    return UNKNOWN


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _estimate_dpi(img_w: int, img_h: int) -> float:
    """
    Estimate image DPI assuming the image depicts an A4 page.
    Uses the longer dimension against A4 landscape width.
    """
    long_side_px = max(img_w, img_h)
    long_side_mm = max(_A4_W_MM, _A4_H_MM)
    dpi = long_side_px / (long_side_mm / 25.4)
    return max(72.0, dpi)


def _mm2_to_px2(mm2: float, dpi: float) -> float:
    """Convert area in mm² to area in px²."""
    mm_per_px = 25.4 / dpi
    return mm2 / (mm_per_px ** 2)


def _point_in_corner(
    cx: int,
    cy: int,
    img_w: int,
    img_h: int,
    corner: str,
) -> bool:
    """Check whether a point lies in the specified corner region."""
    xr, yr = _CORNER_REGIONS[corner]
    x_lo, x_hi = xr[0] * img_w, xr[1] * img_w
    y_lo, y_hi = yr[0] * img_h, yr[1] * img_h
    return x_lo <= cx <= x_hi and y_lo <= cy <= y_hi


def _get_corner_map(meta: Dict[str, Any]) -> Dict[int, Dict[str, str]]:
    """
    Extract corner-marker mapping from meta if present, else use defaults.

    Expected meta structure:
        meta["markers"]["corners"] = {
            "TL": {"type": "square", "position": {"x": ..., "y": ...}},
            ...
        }
    """
    markers_meta = meta.get("markers") or {}
    # v9 meta: TL/TR/BL/BR directly under markers (no "corners" wrapper)
    if any(k in markers_meta for k in ("TL", "TR", "BL", "BR")):
        corners_meta = markers_meta
    else:
        corners_meta = markers_meta.get("corners") or {}

    if not corners_meta:
        return _DEFAULT_CORNER_MAP

    # Build orientation-0 map from meta
    base_map: Dict[str, str] = {}
    for corner_name in ("TL", "TR", "BR", "BL"):
        cm = corners_meta.get(corner_name) or {}
        mtype = str(cm.get("type", "")).lower().strip()
        if mtype in _ALL_MARKER_TYPES:
            base_map[corner_name] = mtype

    if len(base_map) < 4:
        return _DEFAULT_CORNER_MAP

    # Generate all 4 rotations
    corner_order = ["TL", "TR", "BR", "BL"]
    result: Dict[int, Dict[str, str]] = {}
    for rot_idx, angle in enumerate([0, 90, 180, 270]):
        mapping: Dict[str, str] = {}
        for i, corner_name in enumerate(corner_order):
            # Under CW rotation by `rot_idx * 90°`, the marker originally
            # at corner_order[(i + rot_idx) % 4] moves to corner_order[i]
            src_corner = corner_order[(i + rot_idx) % 4]
            mapping[corner_name] = base_map[src_corner]
        result[angle] = mapping

    return result


def _get_marker_positions_px(
    meta: Dict[str, Any],
    out_w: int,
    out_h: int,
) -> Dict[str, Tuple[float, float]]:
    """
    Extract expected marker center positions in output pixel space from meta.

    Falls back to corner offsets if meta doesn't specify explicit positions.
    """
    markers_meta = meta.get("markers") or {}
    # Support both nested and flat structure
    corners_meta = markers_meta.get("corners") or {}
    if not corners_meta and any(k in markers_meta for k in ("TL", "TR", "BR", "BL")):
        corners_meta = markers_meta

    page = meta.get("page") or {}
    size = page.get("size") or page
    page_w_mm = float(size.get("width") or _A4_W_MM)
    page_h_mm = float(size.get("height") or _A4_H_MM)

    sx = out_w / page_w_mm
    sy = out_h / page_h_mm

    positions: Dict[str, Tuple[float, float]] = {}
    for corner_name in ("TL", "TR", "BR", "BL"):
        cm = corners_meta.get(corner_name) or {}
        pos = cm.get("position") or cm.get("center") or {}
        x_mm = float(pos.get("x") or 0.0)
        y_mm = float(pos.get("y") or 0.0)

        if x_mm > 0 and y_mm > 0:
            positions[corner_name] = (x_mm * sx, y_mm * sy)

    # Fallback: place markers at 5% insets from edges
    if len(positions) < 4:
        inset_x = out_w * 0.05
        inset_y = out_h * 0.05
        positions = {
            "TL": (inset_x, inset_y),
            "TR": (out_w - inset_x, inset_y),
            "BR": (out_w - inset_x, out_h - inset_y),
            "BL": (inset_x, out_h - inset_y),
        }

    return positions


# ---------------------------------------------------------------------------
# Candidate extraction
# ---------------------------------------------------------------------------

@dataclass
class _Candidate:
    """Internal candidate blob for marker matching."""
    contour: np.ndarray
    center: Tuple[int, int]
    area: float
    marker_type: str
    corner: str  # assigned corner ("" if unassigned)
    confidence: float = 0.0


def _extract_candidates(
    gray: np.ndarray,
    min_area_px2: float,
    max_area_px2: float,
) -> List[_Candidate]:
    """
    Extract and classify candidate marker blobs from a grayscale image.

    Uses RETR_TREE contour retrieval to handle images where a page border
    connects all content into one outer contour, hiding inner markers.
    Multiple thresholding strategies are tried for robustness.
    """
    img_h, img_w = gray.shape[:2]
    # Dedup candidates by center position
    seen: Dict[Tuple[int, int], _Candidate] = {}

    def _process(contours: Sequence[np.ndarray]) -> None:
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area_px2 or area > max_area_px2:
                continue
            # Reject overly complex contours — real markers are simple shapes
            # (square≈4, L/T/plus≈8-20 points). Noise blobs have 100+ points.
            if len(cnt) > 80:
                continue

            M = cv2.moments(cnt)
            if M["m00"] == 0:
                continue
            cx = int(M["m10"] / M["m00"])
            cy = int(M["m01"] / M["m00"])

            # Spatial filter: must be in outer 15% band
            in_band = (
                cx < img_w * 0.15 or cx > img_w * 0.85
                or cy < img_h * 0.15 or cy > img_h * 0.85
            )
            if not in_band:
                continue

            mtype = classify_blob(cnt)
            if mtype == UNKNOWN:
                continue

            key = (cx, cy)
            if key not in seen or area > seen[key].area:
                seen[key] = _Candidate(
                    contour=cnt,
                    center=(cx, cy),
                    area=area,
                    marker_type=mtype,
                    corner="",
                )

    def _has_all_corners() -> bool:
        """Quick check if candidates cover all 4 corner regions."""
        corners_hit = set()
        for cand in seen.values():
            cx, cy = cand.center
            for corner_name, (xr, yr) in _CORNER_REGIONS.items():
                x_lo, x_hi = xr[0] * img_w, xr[1] * img_w
                y_lo, y_hi = yr[0] * img_h, yr[1] * img_h
                if x_lo <= cx <= x_hi and y_lo <= cy <= y_hi:
                    corners_hit.add(corner_name)
        return len(corners_hit) >= 4

    # Strategy 1: Adaptive threshold + RETR_TREE
    binary_adapt = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=31,
        C=10,
    )
    contours_a, _ = cv2.findContours(
        binary_adapt, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE,
    )
    _process(contours_a)

    # Early exit: if we already have candidates in all 4 corners, skip extras
    if _has_all_corners():
        return list(seen.values())

    # Strategy 2: Otsu threshold + RETR_TREE
    _, binary_otsu = cv2.threshold(
        gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
    )
    contours_o, _ = cv2.findContours(
        binary_otsu, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE,
    )
    _process(contours_o)

    if _has_all_corners():
        return list(seen.values())

    # Strategy 3: Fixed threshold + RETR_LIST (broad fallback)
    _, binary_fix = cv2.threshold(gray, 128, 255, cv2.THRESH_BINARY_INV)
    contours_f, _ = cv2.findContours(
        binary_fix, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE,
    )
    _process(contours_f)

    return list(seen.values())


# Types that are interchangeable during matching (thin-stroke junction shapes)
_JUNCTION_TYPES = frozenset({L_SHAPE, T_SHAPE})


def _types_compatible(detected: str, expected: str) -> bool:
    """Check if a detected type is compatible with an expected type.

    L_SHAPE and T_SHAPE are treated as interchangeable because thin-stroke
    versions are topologically identical and cannot be distinguished by
    blob analysis alone.
    """
    if detected == expected:
        return True
    if detected in _JUNCTION_TYPES and expected in _JUNCTION_TYPES:
        return True
    return False


def _assign_candidates_to_corners(
    candidates: List[_Candidate],
    img_w: int,
    img_h: int,
    corner_map: Dict[str, str],
) -> Dict[str, _Candidate]:
    """
    Assign candidates to corners by matching expected marker types.

    For each corner, pick the candidate that:
    1. Has a compatible marker_type per corner_map (L/T are interchangeable)
    2. Lies within the corner's spatial region
    3. Has the largest area (most confident detection)
    """
    assigned: Dict[str, _Candidate] = {}

    for corner_name, expected_type in corner_map.items():
        best: Optional[_Candidate] = None
        best_area = 0.0

        for cand in candidates:
            if not _types_compatible(cand.marker_type, expected_type):
                continue
            if not _point_in_corner(cand.center[0], cand.center[1], img_w, img_h, corner_name):
                continue
            if cand.area > best_area:
                best = cand
                best_area = cand.area

        if best is not None:
            best.corner = corner_name
            # Override the detected type with the expected type from the map
            best.marker_type = expected_type
            # Confidence based on how close to ideal position (center of corner region)
            xr, yr = _CORNER_REGIONS[corner_name]
            ideal_x = (xr[0] + xr[1]) / 2 * img_w
            ideal_y = (yr[0] + yr[1]) / 2 * img_h
            dist = math.sqrt((best.center[0] - ideal_x) ** 2 + (best.center[1] - ideal_y) ** 2)
            max_dist = math.sqrt((0.15 * img_w) ** 2 + (0.15 * img_h) ** 2)
            best.confidence = max(0.0, 1.0 - dist / max_dist) if max_dist > 0 else 0.5
            assigned[corner_name] = best

    return assigned


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def detect_markers(
    image_bgr: np.ndarray,
    meta: Dict[str, Any],
    *,
    min_area_mm2: float = 2.0,
    max_area_mm2: float = 30.0,
) -> MarkerDetectionResult:
    """
    Detect asymmetric v9 corner markers and determine page orientation.

    Args:
        image_bgr: Input BGR image (any resolution, any orientation).
        meta: Template meta dict containing marker definitions and page size.
        min_area_mm2: Minimum marker area in mm² to consider.
        max_area_mm2: Maximum marker area in mm² to consider.

    Returns:
        MarkerDetectionResult with detected markers, orientation, and success flag.
    """
    if image_bgr is None or image_bgr.size == 0:
        logger.warning("marker_detector: empty input image")
        return MarkerDetectionResult()

    img_h, img_w = image_bgr.shape[:2]
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)

    # Scale-aware area thresholds
    dpi = _estimate_dpi(img_w, img_h)
    min_area_px2 = _mm2_to_px2(min_area_mm2, dpi)
    max_area_px2 = _mm2_to_px2(max_area_mm2, dpi)

    logger.debug(
        "marker_detector: img=%dx%d dpi=%.0f area_range_px=[%.0f, %.0f]",
        img_w, img_h, dpi, min_area_px2, max_area_px2,
    )

    # Extract candidates
    candidates = _extract_candidates(gray, min_area_px2, max_area_px2)
    logger.debug("marker_detector: %d candidates after filtering", len(candidates))

    if not candidates:
        logger.info("marker_detector: no candidates found, returning fallback")
        return MarkerDetectionResult()

    # Try each orientation and find the best assignment
    corner_maps = _get_corner_map(meta)

    best_result: Optional[MarkerDetectionResult] = None
    best_score = -1.0

    for angle, corner_map in corner_maps.items():
        assigned = _assign_candidates_to_corners(candidates, img_w, img_h, corner_map)
        n_matched = len(assigned)

        if n_matched == 0:
            continue

        # Score: number of corners matched + average confidence
        avg_conf = sum(c.confidence for c in assigned.values()) / n_matched
        score = n_matched * 10.0 + avg_conf  # heavily weight number of corners

        if score > best_score:
            best_score = score
            markers_dict: Dict[str, DetectedMarker] = {}
            for corner_name, cand in assigned.items():
                markers_dict[corner_name] = DetectedMarker(
                    corner=corner_name,
                    marker_type=cand.marker_type,
                    center_px=cand.center,
                    contour=cand.contour,
                    confidence=cand.confidence,
                )

            best_result = MarkerDetectionResult(
                markers=markers_dict,
                orientation=angle,
                success=(n_matched == 4),
                method="marker" if n_matched >= 3 else "fallback",
            )

    if best_result is not None and best_result.success:
        logger.info(
            "marker_detector: success orientation=%d markers=%s",
            best_result.orientation,
            list(best_result.markers.keys()),
        )
        return best_result

    # Partial result — return best attempt even if not all 4 detected
    if best_result is not None:
        logger.info(
            "marker_detector: partial detection (%d/4) orientation=%d",
            len(best_result.markers), best_result.orientation,
        )
        return best_result

    logger.info("marker_detector: no markers matched any orientation")
    return MarkerDetectionResult()
