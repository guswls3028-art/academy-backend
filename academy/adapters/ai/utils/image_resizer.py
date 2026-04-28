"""
이미지 리사이징 유틸리티

대용량 이미지 처리 전 리사이징으로 성능 향상 및 메모리 사용량 감소
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple
import cv2  # type: ignore
import numpy as np  # type: ignore

logger = logging.getLogger(__name__)


def imread_exif_aware(path: str) -> Optional[np.ndarray]:
    """cv2.imread + EXIF orientation 자동 보정.

    cv2.imread는 EXIF rotation 메타데이터를 읽지 않아 휴대폰 사진이 회전된
    상태로 잘못 처리되는 경우가 많다. PIL로 먼저 열어 ImageOps.exif_transpose
    로 정상 방향으로 돌린 후 BGR ndarray로 변환.

    Returns: BGR np.ndarray 또는 None (읽기 실패 시).
    """
    try:
        from PIL import Image, ImageOps
    except ImportError:
        # PIL 없으면 cv2 fallback
        return cv2.imread(path)

    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)
            if im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            arr = np.array(im)
        if arr.ndim == 2:
            return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
        # PIL은 RGB → cv2는 BGR
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    except Exception:
        logger.warning("imread_exif_aware: PIL load failed, falling back to cv2 (path=%s)", path, exc_info=True)
        return cv2.imread(path)


def resize_if_large(
    image: np.ndarray,
    max_width: int = 1920,
    max_height: int = 1920,
    max_megapixels: float = 4.0,  # 4MP (약 2000x2000)
) -> Tuple[np.ndarray, bool]:
    """
    이미지가 크면 리사이징
    
    Args:
        image: 입력 이미지 (BGR 또는 Grayscale)
        max_width: 최대 너비
        max_height: 최대 높이
        max_megapixels: 최대 메가픽셀 수
        
    Returns:
        tuple: (리사이징된 이미지, 리사이징 여부)
    """
    h, w = image.shape[:2]
    current_mp = (w * h) / 1_000_000
    
    # 메가픽셀 체크
    if current_mp <= max_megapixels and w <= max_width and h <= max_height:
        return image, False
    
    # 리사이징 비율 계산
    scale_w = max_width / w if w > max_width else 1.0
    scale_h = max_height / h if h > max_height else 1.0
    scale_mp = np.sqrt(max_megapixels / current_mp) if current_mp > max_megapixels else 1.0
    
    # 가장 작은 스케일 사용 (가장 제한적인 조건)
    scale = min(scale_w, scale_h, scale_mp)
    
    new_w = int(w * scale)
    new_h = int(h * scale)
    
    logger.info(
        "Resizing image: %dx%d -> %dx%d (scale=%.2f, mp=%.2f -> %.2f)",
        w,
        h,
        new_w,
        new_h,
        scale,
        current_mp,
        (new_w * new_h) / 1_000_000,
    )
    
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
    return resized, True


def resize_to_fit(
    image: np.ndarray,
    target_width: Optional[int] = None,
    target_height: Optional[int] = None,
    maintain_aspect: bool = True,
) -> np.ndarray:
    """
    이미지를 지정된 크기에 맞게 리사이징
    
    Args:
        image: 입력 이미지
        target_width: 목표 너비
        target_height: 목표 높이
        maintain_aspect: 종횡비 유지 여부
        
    Returns:
        np.ndarray: 리사이징된 이미지
    """
    h, w = image.shape[:2]
    
    if target_width is None and target_height is None:
        return image
    
    if maintain_aspect:
        if target_width and target_height:
            # 둘 다 지정된 경우, 작은 쪽에 맞춤
            scale_w = target_width / w
            scale_h = target_height / h
            scale = min(scale_w, scale_h)
        elif target_width:
            scale = target_width / w
        elif target_height:
            scale = target_height / h
        else:
            return image
        
        new_w = int(w * scale)
        new_h = int(h * scale)
    else:
        new_w = target_width or w
        new_h = target_height or h
    
    if new_w == w and new_h == h:
        return image
    
    return cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)
