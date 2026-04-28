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


def segment_questions_opencv(image_path: str) -> List[BBox]:
    """
    프로젝션 기반 문항 세그멘테이션.
    입력: image_path
    출력: [(x, y, w, h), ...] — 문항 영역 바운딩 박스 (좌상단 기준)
    """
    image_bgr = cv2.imread(image_path)
    if image_bgr is None:
        return []

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
