# apps/worker/omr/warp.py
"""
OMR v9 이미지 정렬 — 마커 기반 homography + fallback chain.

정렬 방법 우선순위:
1. v9 비대칭 마커 검출 → homography warp
2. 문서 외곽 contour 검출 → perspective warp (v8 fallback)
3. portrait 감지 시 90° 회전 + resize (최후 fallback)
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

from apps.worker.ai_worker.ai.omr.marker_detector import (
    detect_markers,
    MarkerDetectionResult,
)

logger = logging.getLogger(__name__)

# A4 landscape at 300 DPI (default output)
_DEFAULT_OUT_SIZE = (3508, 2480)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AlignmentResult:
    """Result of page alignment."""
    image: np.ndarray  # aligned image (BGR)
    success: bool = False
    method: str = "raw"  # "marker_homography", "contour_warp", "rotation_only", "raw"
    orientation: int = 0  # detected orientation (0/90/180/270)
    residual_error: float = float("inf")  # alignment quality metric (lower is better)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _order_points(pts: np.ndarray) -> np.ndarray:
    """
    Order 4 points as: top-left, top-right, bottom-right, bottom-left.
    """
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]  # top-left (smallest x+y)
    rect[2] = pts[np.argmax(s)]  # bottom-right (largest x+y)

    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]  # top-right (smallest x-y → y small, x large)
    rect[3] = pts[np.argmax(diff)]  # bottom-left (largest x-y → y large, x small)
    return rect


def _build_dst_corners(out_w: int, out_h: int) -> np.ndarray:
    """Destination rectangle corners in standard TL-TR-BR-BL order."""
    return np.array(
        [
            [0.0, 0.0],
            [out_w - 1.0, 0.0],
            [out_w - 1.0, out_h - 1.0],
            [0.0, out_h - 1.0],
        ],
        dtype=np.float32,
    )


def _compute_residual(
    H: np.ndarray,
    src_pts: np.ndarray,
    dst_pts: np.ndarray,
) -> float:
    """
    Compute mean reprojection error (in pixels) for a homography.
    """
    if H is None or src_pts is None or dst_pts is None:
        return float("inf")

    src_h = np.hstack([src_pts, np.ones((len(src_pts), 1))]).T  # (3, N)
    projected = H @ src_h  # (3, N)
    projected = projected[:2] / (projected[2:3] + 1e-12)  # (2, N)
    projected = projected.T  # (N, 2)

    errors = np.sqrt(np.sum((projected - dst_pts) ** 2, axis=1))
    return float(np.mean(errors))


def _get_marker_dst_points(
    meta: Dict[str, Any],
    out_w: int,
    out_h: int,
) -> Dict[str, np.ndarray]:
    """
    Build destination points for each corner from meta marker positions.

    Returns dict keyed by corner name ("TL", "TR", "BR", "BL")
    with 2D coordinate arrays.
    """
    markers_meta = meta.get("markers") or {}
    if any(k in markers_meta for k in ("TL", "TR", "BL", "BR")):
        corners_meta = markers_meta
    else:
        corners_meta = markers_meta.get("corners") or {}
    page = meta.get("page") or {}
    size = page.get("size") or page
    page_w_mm = float(size.get("width") or 297.0)
    page_h_mm = float(size.get("height") or 210.0)

    sx = out_w / page_w_mm
    sy = out_h / page_h_mm

    positions: Dict[str, np.ndarray] = {}

    for corner_name in ("TL", "TR", "BR", "BL"):
        cm = corners_meta.get(corner_name) or {}
        pos = cm.get("position") or cm.get("center") or {}
        x_mm = float(pos.get("x") or 0.0)
        y_mm = float(pos.get("y") or 0.0)

        if x_mm > 0 and y_mm > 0:
            positions[corner_name] = np.array([x_mm * sx, y_mm * sy], dtype=np.float32)

    # Fallback: 5% inset from edges
    if len(positions) < 4:
        inset_x = out_w * 0.05
        inset_y = out_h * 0.05
        positions = {
            "TL": np.array([inset_x, inset_y], dtype=np.float32),
            "TR": np.array([out_w - inset_x, inset_y], dtype=np.float32),
            "BR": np.array([out_w - inset_x, out_h - inset_y], dtype=np.float32),
            "BL": np.array([inset_x, out_h - inset_y], dtype=np.float32),
        }

    return positions


# ---------------------------------------------------------------------------
# Strategy 1: Marker-based homography
# ---------------------------------------------------------------------------

def _try_marker_homography(
    image_bgr: np.ndarray,
    meta: Dict[str, Any],
    out_w: int,
    out_h: int,
) -> Optional[AlignmentResult]:
    """
    Attempt marker-based alignment using v9 asymmetric markers.

    Returns AlignmentResult on success, None on failure.
    """
    detection = detect_markers(image_bgr, meta)

    # 3개 이상이면 시도 (4개 perspective / 3개 affine). 3개 미만만 실패.
    if len(detection.markers) < 3:
        logger.debug(
            "warp: marker detection insufficient (%d/4 markers)",
            len(detection.markers),
        )
        return None

    # ── Remap corner labels from image-space to document-space ──
    # The marker detector assigns labels based on which image corner region
    # each marker was found in (image-space). For non-zero orientations,
    # image-TL does NOT contain the document-TL marker. The rotation maps
    # cycle clockwise [TL, TR, BR, BL], so image corner i contains the
    # document marker at (i + rot_steps) % 4.
    markers = detection.markers
    orientation = detection.orientation
    if orientation != 0:
        _CO = ["TL", "TR", "BR", "BL"]
        rot_steps = {90: 1, 180: 2, 270: 3}.get(orientation, 0)
        remapped: Dict[str, Any] = {}
        for i, img_corner in enumerate(_CO):
            if img_corner in markers:
                doc_corner = _CO[(i + rot_steps) % 4]
                remapped[doc_corner] = markers[img_corner]
        markers = remapped
        logger.info(
            "warp: remapped corners for orientation=%d: %s",
            orientation, list(markers.keys()),
        )

    # Build source points from detected marker centers
    dst_map = _get_marker_dst_points(meta, out_w, out_h)

    # Collect matching pairs (source=detected, destination=expected)
    src_pts: List[np.ndarray] = []
    dst_pts: List[np.ndarray] = []
    corner_order = ["TL", "TR", "BR", "BL"]

    for corner in corner_order:
        marker = markers.get(corner)
        dst_pt = dst_map.get(corner)
        if marker is None or dst_pt is None:
            continue
        src_pts.append(np.array(marker.center_px, dtype=np.float32))
        dst_pts.append(dst_pt)

    if len(src_pts) < 3:
        logger.debug("warp: not enough marker pairs (%d)", len(src_pts))
        return None

    src_arr = np.array(src_pts, dtype=np.float32)
    dst_arr = np.array(dst_pts, dtype=np.float32)

    # 4개: perspective homography (종이 휨/각도 보정 가능)
    # 3개: affine transform (회전+스케일+평행이동만, 페이지가 거의 평면이면 충분)
    method_name = "marker_homography"
    if len(src_pts) >= 4:
        H, _mask = cv2.findHomography(src_arr, dst_arr, cv2.RANSAC, 5.0)
    else:
        M_affine = cv2.getAffineTransform(src_arr[:3], dst_arr[:3])
        H = np.vstack([M_affine, [0.0, 0.0, 1.0]]).astype(np.float64)
        method_name = "marker_affine_3pt"

    if H is None:
        logger.warning("warp: homography/affine returned None")
        return None

    warped = cv2.warpPerspective(image_bgr, H, (out_w, out_h))
    residual = _compute_residual(H, src_arr, dst_arr)

    logger.info(
        "warp: %s success orientation=%d residual=%.2f (%d markers)",
        method_name, detection.orientation, residual, len(src_pts),
    )

    return AlignmentResult(
        image=warped,
        success=True,
        method=method_name,
        orientation=detection.orientation,
        residual_error=residual,
    )


# ---------------------------------------------------------------------------
# Strategy 2: Contour-based perspective warp (v8 fallback)
# ---------------------------------------------------------------------------

def _try_contour_warp(
    image_bgr: np.ndarray,
    out_w: int,
    out_h: int,
) -> Optional[AlignmentResult]:
    """
    Find the largest quadrilateral contour and warp to output size.
    This is the v8 approach — works well for clean scans with visible page edges.
    """
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)

    # Strengthen edges
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges = cv2.dilate(edges, kernel, iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contours = sorted(contours, key=cv2.contourArea, reverse=True)

    page_cnt = None
    for cnt in contours[:8]:
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) == 4:
            page_cnt = approx
            break

    if page_cnt is None:
        return None

    pts = page_cnt.reshape(4, 2).astype(np.float32)
    rect = _order_points(pts)

    dst = _build_dst_corners(out_w, out_h)
    M = cv2.getPerspectiveTransform(rect, dst)
    warped = cv2.warpPerspective(image_bgr, M, (out_w, out_h))

    # Compute residual for quality metric
    residual = _compute_residual(M, rect, dst)

    logger.info("warp: contour_warp success residual=%.2f", residual)

    return AlignmentResult(
        image=warped,
        success=True,
        method="contour_warp",
        orientation=0,
        residual_error=residual,
    )


# ---------------------------------------------------------------------------
# Strategy 3: Simple rotation + resize
# ---------------------------------------------------------------------------

def _try_rotation_only(
    image_bgr: np.ndarray,
    out_w: int,
    out_h: int,
) -> Optional[AlignmentResult]:
    """
    If the image is portrait, rotate 90° CW and resize.
    For landscape images that failed contour detection, just resize.
    """
    h, w = image_bgr.shape[:2]

    if h > w:
        # Portrait → landscape via 90° CW rotation
        rotated = cv2.rotate(image_bgr, cv2.ROTATE_90_CLOCKWISE)
        resized = cv2.resize(rotated, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
        logger.info("warp: rotation_only (portrait→landscape)")
        return AlignmentResult(
            image=resized,
            success=True,
            method="rotation_only",
            orientation=90,
            residual_error=float("inf"),
        )

    return None


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

def align_to_a4_landscape(
    *,
    image_bgr: np.ndarray,
    meta: Dict[str, Any],
    out_size_px: Tuple[int, int] = _DEFAULT_OUT_SIZE,
) -> AlignmentResult:
    """
    Align an input image to A4 landscape using a prioritised fallback chain.

    Priority:
        1. v9 asymmetric marker detection → homography warp
        2. Document contour detection → perspective warp (v8 fallback)
        3. Portrait detection → 90° CW rotation + resize
        4. Raw resize (last resort)

    Args:
        image_bgr: Input BGR image (any resolution, any orientation).
        meta: Template meta dict with marker/page definitions.
        out_size_px: Output size as (width, height) in pixels.
            Default: (3508, 2480) — A4 landscape at 300 DPI.

    Returns:
        AlignmentResult with the aligned image and metadata about the method used.
    """
    if image_bgr is None or image_bgr.size == 0:
        logger.error("warp: empty input image")
        empty = np.zeros((out_size_px[1], out_size_px[0], 3), dtype=np.uint8)
        return AlignmentResult(image=empty)

    out_w, out_h = out_size_px

    # --- Strategy 1: Marker-based homography ---
    try:
        result = _try_marker_homography(image_bgr, meta, out_w, out_h)
        if result is not None:
            return result
    except Exception:
        logger.exception("warp: marker_homography failed with exception")

    # --- Strategy 2: Contour-based perspective warp ---
    try:
        result = _try_contour_warp(image_bgr, out_w, out_h)
        if result is not None:
            return result
    except Exception:
        logger.exception("warp: contour_warp failed with exception")

    # --- Strategy 3: Rotation + resize ---
    try:
        result = _try_rotation_only(image_bgr, out_w, out_h)
        if result is not None:
            return result
    except Exception:
        logger.exception("warp: rotation_only failed with exception")

    # --- Strategy 4: Raw resize (last resort) ---
    logger.warning("warp: all strategies failed, returning raw resize")
    resized = cv2.resize(image_bgr, (out_w, out_h), interpolation=cv2.INTER_LINEAR)
    return AlignmentResult(
        image=resized,
        success=False,
        method="raw",
        orientation=0,
        residual_error=float("inf"),
    )


# ---------------------------------------------------------------------------
# Local anchor-based affine alignment (sub-region)
# ---------------------------------------------------------------------------

def local_align_region(
    *,
    image_gray: np.ndarray,
    expected_anchors_px: List[Tuple[int, int]],
    search_radius_px: int = 50,
    anchor_size_range_px: Tuple[int, int] = (10, 40),
) -> Optional[np.ndarray]:
    """
    Local anchor-based affine alignment for a sub-region.

    Detects small black square/circle anchors near expected positions,
    computes an affine correction, and returns the 2x3 affine matrix.

    Used by identifier.py and engine.py for per-block alignment to correct
    minor misalignment after the global page warp.

    Args:
        image_gray: Grayscale image (the full aligned page or a large crop).
        expected_anchors_px: List of (x, y) pixel coordinates where anchors
            are expected to appear.
        search_radius_px: How far (in pixels) to search around each expected
            position. Default: 50.
        anchor_size_range_px: (min_side, max_side) in pixels for valid anchor
            contours. Default: (10, 40).

    Returns:
        A 2x3 affine matrix (np.ndarray, float64) if at least 2 anchors are
        detected, or None if insufficient anchors found (caller should use
        uncorrected coordinates).
    """
    if image_gray is None or image_gray.size == 0:
        return None
    if len(expected_anchors_px) < 2:
        return None

    img_h, img_w = image_gray.shape[:2]
    min_side, max_side = anchor_size_range_px
    min_area = min_side * min_side
    max_area = max_side * max_side

    detected_pts: List[np.ndarray] = []
    expected_pts: List[np.ndarray] = []

    for ex, ey in expected_anchors_px:
        # Crop search region around expected position
        x1 = max(0, ex - search_radius_px)
        y1 = max(0, ey - search_radius_px)
        x2 = min(img_w, ex + search_radius_px)
        y2 = min(img_h, ey + search_radius_px)

        if x2 <= x1 or y2 <= y1:
            continue

        roi = image_gray[y1:y2, x1:x2]

        # Threshold the ROI
        _, binary = cv2.threshold(roi, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            continue

        # Find best anchor candidate
        best_cnt = None
        best_score = -1.0

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < min_area or area > max_area:
                continue

            # Solidity check: anchors are solid squares or circles
            hull = cv2.convexHull(cnt)
            hull_area = cv2.contourArea(hull)
            solidity = area / hull_area if hull_area > 0 else 0.0
            if solidity < 0.7:
                continue

            # Proximity to ROI center (prefer closest to expected position)
            M = cv2.moments(cnt)
            if M["m00"] == 0:
                continue
            cx_local = M["m10"] / M["m00"]
            cy_local = M["m01"] / M["m00"]

            roi_cx = (x2 - x1) / 2.0
            roi_cy = (y2 - y1) / 2.0
            dist = math.sqrt((cx_local - roi_cx) ** 2 + (cy_local - roi_cy) ** 2)

            # Score: high solidity and close to center → better
            max_dist = math.sqrt(roi_cx ** 2 + roi_cy ** 2) or 1.0
            proximity = 1.0 - (dist / max_dist)
            score = solidity * 0.5 + proximity * 0.5

            if score > best_score:
                best_score = score
                best_cnt = cnt
                # Convert local coordinates back to image coordinates
                best_cx = cx_local + x1
                best_cy = cy_local + y1

        if best_cnt is not None and best_score > 0.3:
            detected_pts.append(np.array([best_cx, best_cy], dtype=np.float64))
            expected_pts.append(np.array([float(ex), float(ey)], dtype=np.float64))

    # Need at least 2 anchor pairs for affine estimation
    if len(detected_pts) < 2:
        logger.debug(
            "local_align: insufficient anchors (%d/%d detected)",
            len(detected_pts), len(expected_anchors_px),
        )
        return None

    src = np.array(detected_pts, dtype=np.float64).reshape(-1, 1, 2)
    dst = np.array(expected_pts, dtype=np.float64).reshape(-1, 1, 2)

    affine_mat, inliers = cv2.estimateAffinePartial2D(src, dst)

    if affine_mat is None:
        logger.debug("local_align: estimateAffinePartial2D returned None")
        return None

    n_inliers = int(np.sum(inliers)) if inliers is not None else 0
    logger.debug(
        "local_align: affine estimated with %d/%d inliers",
        n_inliers, len(detected_pts),
    )

    return affine_mat
