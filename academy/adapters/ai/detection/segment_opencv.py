# apps/worker/ai/detection/segment_opencv.py
"""
OpenCV 기반 시험지 문항 세그멘테이션.

전략: 프로젝션 프로파일 기반 (Projection Profile Cutting)
  1. 구조선(테두리/구분선) 제거
  2. 수직 프로젝션으로 2단 레이아웃 감지 + 컬럼 분리
  3. 컬럼별 수평 프로젝션으로 문항 간 빈 줄(gap) 탐지
  4. gap 기반으로 콘텐츠 영역(문항) 분할
  5. 인접한 작은 영역 병합 + 노이즈 필터

기존 dilation+contour 방식은 실제 시험지(2단, 그림 포함)에서
전체가 하나의 contour로 병합되어 문항 분리 불가.
"""
from __future__ import annotations

from typing import List, Tuple

import cv2  # type: ignore
import numpy as np  # type: ignore

BBox = Tuple[int, int, int, int]


def _apply_lab_clahe_mild(img_bgr: np.ndarray) -> np.ndarray:
    """LAB color space의 L 채널에 mild CLAHE 적용 — 좌표계 보존, 그림자/조명 불균일 보정.

    Stage 6.3P dry-run 측정 (3 sample / scan PDF):
      - contour count +67% (285 → 476)
      - text_density +54%
      - bbox IoU 0.988 (좌표 변동 거의 없음)
      - clipLimit=2.0 / tileGridSize=(8,8) 가 best 임계 (clipLimit 4.0+ 은 노이즈 폭증).

    카메라 사진 단독 적용은 IoU 0.18 (deskew 결합 필요) — Stage 6.3R 영역.

    좌표계 안전성: LAB 변환 → L 채널 in-place equalize → BGR 역변환. resize/rotate/warp 0.
    """
    if img_bgr is None or len(img_bgr.shape) != 3:
        return img_bgr
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB)
    L, A, B = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    L_eq = clahe.apply(L)
    return cv2.cvtColor(cv2.merge((L_eq, A, B)), cv2.COLOR_LAB2BGR)


def detect_chapter_divider(image_path: str) -> bool:
    """페이지가 chapter divider (단일 색 background) 인지 추정.

    Audit 결함 (2026-05-04 PHASE_FULL_AUDIT): commercial 6 doc + workbook part 학습자료
    (doc#312, #303 등)에서 chapter divider 페이지(분홍/파랑/녹색 단일 background +
    큰 헤더 텍스트)가 problem으로 인덱싱됨. anchor splitter가 본문과 동일 취급.

    원리:
    - HSV saturation > 30 픽셀 비율 (색상 있는 영역) ≥ 30% — 본문(흰색)이 아님
    - dominant hue (가장 많이 나오는 색상) 비율 ≥ 50% — 단일 색 background
    - 두 조건 모두 충족 시 chapter divider 판정 → paper_type=non_question 강제

    False positive 위험: 큰 그림/지도/차트가 dominant color 가질 수 있음. 임계값
    50%는 보수적 (전체 페이지의 절반 이상이 같은 색일 때만 trigger). 본문 페이지에
    그림 있어도 보통 30~40% 수준.

    Returns: True (chapter divider 추정) / False (본문).
    이미지 못 읽으면 False (안전 폴백 — 본문 처리).
    """
    img = cv2.imread(image_path)
    if img is None:
        return False
    h, w = img.shape[:2]
    if h < 100 or w < 100:
        return False

    # HSV 변환
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    s = hsv[:, :, 1]

    # 1. saturation > 30인 픽셀 (색상 있는 영역) 비율
    colored_ratio = float((s > 30).sum()) / (h * w)
    if colored_ratio < 0.30:
        return False  # white background 본문 페이지

    # 2. dominant hue 비율
    hue = hsv[:, :, 0][s > 30]  # 색상 있는 픽셀의 hue만
    if len(hue) < 1000:
        return False  # 색상 픽셀 너무 적음
    hist = np.bincount(hue, minlength=180)
    top_bin_ratio = float(hist.max()) / hist.sum()

    return bool(top_bin_ratio >= 0.50)  # 단일 색이 50%+ → chapter divider


def estimate_handwriting_score(image_path: str) -> float:
    """페이지 이미지에서 손글씨 비율 추정 [0.0, 1.0] (A-2 POC, 2026-05-04).

    원리: edge orientation entropy.
    - 인쇄 텍스트는 axis-aligned (0°/90°/45°/135° 등 quantized) → 낮은 entropy
    - 손글씨는 random direction strokes → 높은 entropy
    Sobel gradient → 강한 edge만 → 방향 분포 → entropy 정규화.

    classify_paper_type의 handwriting_score 인자에 주입하여 STUDENT_ANSWER_PHOTO
    분기 (>= 0.78 임계). source_type=student_exam_photo의 hardcoded 0.85 대신
    페이지별 측정값 사용 → 깨끗 페이지(인쇄본만)는 SCAN_DUAL/SCAN_SINGLE로 분류
    되어 anchor splitter 사용 가능 → 매치업 정확도 페이지→문항 단위 복귀.

    POC 측정 결과 (T2 데이터, 2026-05-04):
    - exam_photo (학생답안지) n=43: mean=0.905
    - workbook (인쇄 PDF) n=82: mean=0.864
    - 평균 차이 0.04 — 단순 임계로 두 도메인 분리 어려움 (false positive 100%).
    → entropy 단독 신호 불충분. 다음 cycle: stroke width variance 추가 또는 CNN 학습.

    현재 운영 통합 X — classify_paper_type에서 source_type bias (A-1, hardcoded 0.85)를
    그대로 사용. 이 함수는 라이브러리에 보관 (다음 detector 시도 시 비교 baseline).

    Returns: 0.0 (인쇄본만) ~ 1.0 (손글씨 dominant).
    이미지 못 읽으면 0.0 (안전 폴백 — heuristic 다른 신호로 결정).
    """
    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return 0.0
    h, w = img.shape[:2]
    if h < 100 or w < 100:
        return 0.0

    # 1. blur — 노이즈 제거 (스캐너 dust, JPEG artifact)
    blur = cv2.GaussianBlur(img, (3, 3), 0)

    # 2. Sobel gradient (x, y)
    sx = cv2.Sobel(blur, cv2.CV_64F, 1, 0, ksize=3)
    sy = cv2.Sobel(blur, cv2.CV_64F, 0, 1, ksize=3)
    mag = np.sqrt(sx ** 2 + sy ** 2)

    # 3. 강한 edge만 (상위 5% threshold) — 텍스트 + 손글씨 픽셀
    if mag.max() < 1.0:  # 거의 빈 페이지
        return 0.0
    threshold = float(np.percentile(mag, 95))
    edges = mag > threshold
    edge_count = int(edges.sum())
    if edge_count < 100:
        return 0.0

    # 4. edge orientation 분포 (18 bins, -π~π)
    angles = np.arctan2(sy[edges], sx[edges])
    hist, _ = np.histogram(angles, bins=18, range=(-np.pi, np.pi))
    if hist.sum() == 0:
        return 0.0
    p = hist / hist.sum()

    # 5. entropy 계산 → max entropy(log 18) 정규화
    entropy = -float((p * np.log(p + 1e-9)).sum())
    max_entropy = float(np.log(18))
    score = entropy / max_entropy

    # POC 보정: 인쇄 텍스트는 0.6~0.7 범위, 손글씨는 0.8+ 관찰 (T2 시험지 측정 후 조정).
    # 일단 raw score 반환 — 호출자(classify_paper_type)가 0.78 임계로 판정.
    return float(min(1.0, max(0.0, score)))


def detect_dual_column_pixel(image_path: str, *, apply_clahe: bool = False) -> bool:
    """이미지 픽셀 기반 dual-col 감지 (paper_type 분류기 백업 신호).

    OCR 블록 분포 휴리스틱이 부족한 폰사진/저해상도 스캔본에서 dual-col을
    잡기 위한 백업. 기존 _detect_columns 로직 재사용.

    apply_clahe: dispatcher 의 ENV gate 가 결정 (Stage 6.3Q). scan/photo 경로에서만 True.
    """
    image_bgr = cv2.imread(image_path)
    if image_bgr is None:
        return False
    if apply_clahe:
        image_bgr = _apply_lab_clahe_mild(image_bgr)
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    h_img, w_img = gray.shape[:2]
    if w_img < 200 or h_img < 200:
        return False
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(
        blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )
    clean = _remove_structural_lines(thresh, w_img, h_img)
    columns = _detect_columns(clean, w_img, h_img)
    return len(columns) >= 2


def segment_questions_opencv(image_path: str, *, apply_clahe: bool = False) -> List[BBox]:
    """
    프로젝션 기반 문항 세그멘테이션.
    입력: image_path
    출력: [(x, y, w, h), ...] — 문항 영역 바운딩 박스 (좌상단 기준)

    apply_clahe: scan/photo 경로 신호. dispatcher (segment_dispatcher._should_apply_opencv_clahe)
    가 ENV `MATCHUP_SEGMENT_OPENCV_CLAHE` + has_embedded_text + source_type 으로 결정. clean
    text PDF 페이지에는 항상 False.
    """
    image_bgr = cv2.imread(image_path)
    if image_bgr is None:
        return []

    # Stage 6.3Q: scan/photo 경로 한정 LAB-CLAHE. 좌표계 보존 (in-place equalize 만).
    if apply_clahe:
        image_bgr = _apply_lab_clahe_mild(image_bgr)

    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY)
    h_img, w_img = gray.shape[:2]

    # 이진화
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    _, thresh = cv2.threshold(
        blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
    )

    # 구조선 제거: 페이지 테두리, 컬럼 구분선 등
    clean = _remove_structural_lines(thresh, w_img, h_img)

    # 컬럼 감지
    columns = _detect_columns(clean, w_img, h_img)

    # 컬럼별 문항 영역 추출
    boxes: List[BBox] = []
    for x_start, x_end in columns:
        regions = _find_content_regions(clean, x_start, x_end, h_img)
        for y_start, y_end in regions:
            boxes.append((x_start, y_start, x_end - x_start, y_end - y_start))

    # 정렬: 왼쪽 컬럼 위→아래, 오른쪽 컬럼 위→아래
    mid_x = w_img // 2
    boxes.sort(key=lambda b: (0 if b[0] < mid_x else 1, b[1]))

    return boxes


def segment_questions_scan_layout(image_path: str, *, apply_clahe: bool = False) -> List[BBox]:
    """Handwritten scan-safe school-exam layout fallback.

    Projection over the raw grayscale page is easily polluted by red grading marks
    and pencil 풀이. This fallback suppresses saturated colored strokes, forces the
    common two-column school-exam layout, then merges small intra-question gaps.
    It intentionally returns numberless boxes; OCR/VLM paths can still provide
    numbered regions when available.
    """
    image_bgr = cv2.imread(image_path)
    if image_bgr is None:
        return []
    if apply_clahe:
        image_bgr = _apply_lab_clahe_mild(image_bgr)
    h_img, w_img = image_bgr.shape[:2]
    if w_img < 400 or h_img < 400:
        return []

    mask = _black_print_mask(image_bgr)
    if mask is None:
        return []
    clean = _remove_structural_lines(mask, w_img, h_img)
    columns = _detect_scan_exam_columns(mask, w_img, h_img)

    boxes: List[BBox] = []
    for x_start, x_end in columns:
        regions = _find_content_regions(clean, x_start, x_end, h_img)
        for y_start, y_end in _merge_scan_content_regions(regions, h_img):
            boxes.append((x_start, y_start, x_end - x_start, y_end - y_start))

    boxes = _filter_scan_layout_boxes(boxes, w_img, h_img)
    mid_x = w_img // 2
    boxes.sort(key=lambda b: (0 if b[0] < mid_x else 1, b[1]))
    return boxes


def _black_print_mask(image_bgr: np.ndarray) -> np.ndarray | None:
    """Return dark, low-saturation strokes while suppressing red grading marks."""
    if image_bgr is None or len(image_bgr.shape) != 3:
        return None
    hsv = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2HSV)
    mask = ((hsv[:, :, 2] < 150) & (hsv[:, :, 1] < 90)).astype("uint8") * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def _detect_scan_exam_columns(
    mask: np.ndarray,
    w_img: int,
    h_img: int,
) -> List[Tuple[int, int]]:
    """Detect a central divider when visible, otherwise use a conservative half split."""
    if w_img <= 0 or h_img <= 0:
        return []
    divider_x: int | None = None
    try:
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(80, h_img // 8)))
        v_lines = cv2.morphologyEx(mask, cv2.MORPH_OPEN, v_kernel)
        projection = (v_lines > 0).sum(axis=0)
        center_start = w_img // 3
        center_end = (2 * w_img) // 3
        if center_end > center_start:
            center = projection[center_start:center_end]
            best = int(np.argmax(center))
            candidate = center_start + best
            if projection[candidate] >= h_img * 0.16:
                divider_x = candidate
    except Exception:
        divider_x = None

    if divider_x is None:
        divider_x = w_img // 2
    min_col_w = int(w_img * 0.32)
    if divider_x < min_col_w or (w_img - divider_x) < min_col_w:
        return [(0, w_img)]
    return [(0, divider_x), (divider_x, w_img)]


def _merge_scan_content_regions(
    regions: List[Tuple[int, int]],
    h_img: int,
) -> List[Tuple[int, int]]:
    """Merge intra-question fragments after red/pencil suppression."""
    if not regions or h_img <= 0:
        return []
    kept: List[Tuple[int, int]] = []
    for y0, y1 in sorted(regions):
        height = y1 - y0
        if y0 < h_img * 0.08 and y1 < h_img * 0.16:
            continue
        if height < h_img * 0.045:
            continue
        kept.append((y0, y1))

    merged: List[List[int]] = []
    for y0, y1 in kept:
        if not merged:
            merged.append([y0, y1])
            continue
        gap = y0 - merged[-1][1]
        combined_height = y1 - merged[-1][0]
        if gap <= h_img * 0.025 and combined_height <= h_img * 0.38:
            merged[-1][1] = y1
        else:
            merged.append([y0, y1])
    return [(int(y0), int(y1)) for y0, y1 in merged]


def _filter_scan_layout_boxes(
    boxes: List[BBox],
    w_img: int,
    h_img: int,
) -> List[BBox]:
    """Remove obvious footer/scratch fragments from scan-layout fallback."""
    filtered: List[BBox] = []
    for x, y, w, h in boxes:
        if w <= 0 or h <= 0:
            continue
        if h < h_img * 0.08:
            continue
        if y >= h_img * 0.90:
            continue
        # Very low, short fragments are usually footer notes or leftover 풀이.
        if y >= h_img * 0.82 and h <= h_img * 0.16:
            continue
        if w < w_img * 0.25:
            continue
        filtered.append((x, y, w, h))
    return filtered


def _remove_structural_lines(
    thresh: np.ndarray, w_img: int, h_img: int,
) -> np.ndarray:
    """긴 직선(테두리, 구분선)을 제거하여 텍스트/그림만 남긴다."""
    clean = thresh.copy()

    # 수평선 감지 + 제거 (페이지 폭의 1/5 이상 길이)
    h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (w_img // 5, 1))
    h_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, h_kernel)
    clean[h_lines > 0] = 0

    # 수직선 감지 + 제거 (페이지 높이의 1/5 이상 길이)
    v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, h_img // 5))
    v_lines = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, v_kernel)
    clean[v_lines > 0] = 0

    return clean


def _detect_columns(
    clean: np.ndarray, w_img: int, _h_img: int,
) -> List[Tuple[int, int]]:
    """
    수직 프로젝션 프로파일로 1단/2단 레이아웃 판별.
    중앙 1/3 구간에서 잉크 밀도 최소점을 찾아 컬럼 분리점으로 사용.
    """
    v_proj = np.sum(clean, axis=0).astype(np.float64) / 255
    # 스무딩으로 노이즈 제거
    kernel_size = max(30, w_img // 50)
    v_smooth = np.convolve(v_proj, np.ones(kernel_size) / kernel_size, mode="same")

    # 중앙 1/3 구간에서 최소점 탐색
    center_start = w_img // 3
    center_end = 2 * w_img // 3
    center_proj = v_smooth[center_start:center_end]

    if len(center_proj) == 0:
        return [(0, w_img)]

    min_idx = int(np.argmin(center_proj))
    divider_x = center_start + min_idx
    divider_val = center_proj[min_idx]

    # 전체 평균 대비 분리점의 잉크 밀도가 50% 미만이면 2단
    nonzero = v_smooth[v_smooth > 0]
    avg_val = float(np.mean(nonzero)) if len(nonzero) > 0 else 1.0
    is_dual = divider_val < avg_val * 0.5

    if is_dual:
        return [(0, divider_x), (divider_x, w_img)]
    return [(0, w_img)]


def _find_content_regions(
    clean: np.ndarray,
    x_start: int,
    x_end: int,
    h_img: int,
) -> List[Tuple[int, int]]:
    """
    컬럼 내에서 수평 프로젝션 프로파일로 콘텐츠 영역(문항)을 분리.

    gap = 잉크 밀도가 낮은 수평 띠 (문항 사이의 빈 공간).
    """
    col_strip = clean[:, x_start:x_end]
    col_width = x_end - x_start
    if col_width <= 0:
        return []

    h_proj = np.sum(col_strip, axis=1).astype(np.float64) / 255
    h_proj_norm = h_proj / col_width

    # 스무딩 (문항 내부의 작은 gap 무시)
    smooth_size = max(12, h_img // 200)
    h_smooth = np.convolve(h_proj_norm, np.ones(smooth_size) / smooth_size, mode="same")

    # gap 감지: 잉크 밀도 < 1.5%
    gap_thresh = 0.015
    is_gap = h_smooth < gap_thresh

    # gap 구간 수집 — 낮은 임계값으로 모든 잠재적 문항 경계 포착
    # 실제 시험지에서 문항 간 gap은 18px(@200DPI) 이상.
    # 과분할은 후속 병합 단계에서 처리한다.
    min_gap_len = max(18, h_img // 150)
    gap_regions: List[Tuple[int, int]] = []
    in_gap = False
    gap_start = 0
    for row in range(h_img):
        if is_gap[row] and not in_gap:
            gap_start = row
            in_gap = True
        elif not is_gap[row] and in_gap:
            if row - gap_start >= min_gap_len:
                gap_regions.append((gap_start, row))
            in_gap = False
    if in_gap and h_img - gap_start >= min_gap_len:
        gap_regions.append((gap_start, h_img))

    # gap 사이의 콘텐츠 영역 추출
    raw_regions: List[Tuple[int, int]] = []
    prev_end = 0
    for gs, ge in gap_regions:
        if gs > prev_end:
            raw_regions.append((prev_end, gs))
        prev_end = ge
    if h_img > prev_end:
        raw_regions.append((prev_end, h_img))

    # 너무 작은 영역 병합 (인접 영역으로 흡수)
    min_region_h = max(int(h_img * 0.06), 100)
    merged = _merge_small_regions(raw_regions, min_region_h)

    # 최종 필터: 극소 영역 제거 (페이지의 5% 미만)
    final_min = max(int(h_img * 0.05), 80)
    return [(y0, y1) for y0, y1 in merged if y1 - y0 >= final_min]



def _merge_small_regions(
    regions: List[Tuple[int, int]],
    min_height: int,
) -> List[Tuple[int, int]]:
    """
    min_height 미만인 영역을 가장 가까운 인접 영역에 병합.
    문항 내부의 작은 분할(보기/그림 사이 gap)을 복원.
    """
    if len(regions) <= 1:
        return regions

    merged: List[Tuple[int, int]] = list(regions)
    changed = True
    while changed:
        changed = False
        new_merged: List[Tuple[int, int]] = []
        i = 0
        while i < len(merged):
            y0, y1 = merged[i]
            if y1 - y0 < min_height and len(merged) > 1:
                # 인접 영역과 합치기
                if i + 1 < len(merged):
                    # 다음 영역과 병합
                    ny0, ny1 = merged[i + 1]
                    new_merged.append((y0, ny1))
                    i += 2
                    changed = True
                elif new_merged:
                    # 이전 영역과 병합
                    py0, py1 = new_merged[-1]
                    new_merged[-1] = (py0, y1)
                    i += 1
                    changed = True
                else:
                    new_merged.append((y0, y1))
                    i += 1
            else:
                new_merged.append((y0, y1))
                i += 1
        merged = new_merged

    return merged
