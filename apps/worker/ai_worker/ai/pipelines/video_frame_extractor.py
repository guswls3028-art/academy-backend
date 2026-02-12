"""
비디오 프레임 추출 모듈

요구사항:
- 전체 비디오 프레임 분석 금지
- 1-3 fps로 샘플링
- 페이지 전환 감지 (SSIM/Frame Difference)
- 페이지당 대표 프레임 1개만 추출
- 최대 페이지 수 제한
- 처리 타임아웃 강제
"""

from __future__ import annotations

import logging
import time
from typing import List, Dict, Any, Optional, Tuple
import cv2  # type: ignore
import numpy as np  # type: ignore

try:
    from skimage.metrics import structural_similarity as ssim  # type: ignore
    HAS_SSIM = True
except ImportError:
    HAS_SSIM = False

logger = logging.getLogger(__name__)


def _calculate_ssim(img1: np.ndarray, img2: np.ndarray) -> float:
    """
    두 이미지 간 SSIM 계산
    
    Args:
        img1: 첫 번째 이미지 (grayscale)
        img2: 두 번째 이미지 (grayscale)
        
    Returns:
        float: SSIM 값 (0.0 ~ 1.0)
    """
    if not HAS_SSIM:
        # SSIM이 없으면 Frame Difference 사용
        return 1.0 - _calculate_frame_diff(img1, img2)
    
    try:
        # 이미지 크기 맞추기
        if img1.shape != img2.shape:
            img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
        
        # SSIM 계산
        score = ssim(img1, img2, data_range=255)
        return float(score)
    except Exception as e:
        logger.warning("SSIM calculation failed: %s", e)
        # Fallback to frame diff
        return 1.0 - _calculate_frame_diff(img1, img2)


def _calculate_frame_diff(img1: np.ndarray, img2: np.ndarray) -> float:
    """
    두 프레임 간 차이 계산 (간단한 방법)
    
    Args:
        img1: 첫 번째 프레임 (grayscale)
        img2: 두 번째 프레임 (grayscale)
        
    Returns:
        float: 차이 비율 (0.0 ~ 1.0)
    """
    try:
        # 이미지 크기 맞추기
        if img1.shape != img2.shape:
            img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
        
        # 절대 차이 계산
        diff = cv2.absdiff(img1, img2)
        diff_ratio = np.sum(diff > 30) / diff.size  # 임계값 30
        return float(diff_ratio)
    except Exception as e:
        logger.warning("Frame diff calculation failed: %s", e)
        return 0.0


def extract_key_frames(
    video_path: str,
    target_fps: float = 2.0,  # 1-3 fps 권장
    max_pages: int = 10,  # 최대 페이지 수 제한
    page_change_threshold: float = 0.3,  # 페이지 전환 임계값 (SSIM 또는 diff)
    use_ssim: bool = True,  # SSIM 사용 여부 (False면 frame diff 사용)
    processing_timeout: int = 60,  # 처리 타임아웃 (초)
) -> Dict[str, Any]:
    """
    비디오에서 키 프레임 추출 (페이지당 1개)
    
    Args:
        video_path: 비디오 파일 경로
        target_fps: 목표 FPS (1-3 권장)
        max_pages: 최대 페이지 수
        page_change_threshold: 페이지 전환 감지 임계값
        use_ssim: SSIM 사용 여부
        processing_timeout: 처리 타임아웃 (초)
        
    Returns:
        dict: 추출된 키 프레임 정보
    """
    start_time = time.time()
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {video_path}")
    
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = total_frames / fps if fps > 0 else 0
        
        # 샘플링 간격 계산 (target_fps에 맞춤)
        frame_interval = int(fps / target_fps) if fps > 0 else 1
        frame_interval = max(1, frame_interval)  # 최소 1프레임
        
        logger.info(
            "Video info: fps=%.2f, total_frames=%d, duration=%.2fs, frame_interval=%d",
            fps,
            total_frames,
            duration,
            frame_interval,
        )
        
        key_frames: List[Dict[str, Any]] = []
        prev_frame: Optional[np.ndarray] = None
        current_page_frames: List[Tuple[int, np.ndarray]] = []  # (frame_idx, frame)
        frame_idx = 0
        
        while True:
            # 타임아웃 체크
            if time.time() - start_time > processing_timeout:
                logger.warning("Processing timeout reached: %ds", processing_timeout)
                break
            
            ret, frame = cap.read()
            if not ret:
                break
            
            # 샘플링: target_fps에 맞춰 프레임 선택
            if frame_idx % frame_interval != 0:
                frame_idx += 1
                continue
            
            # Grayscale 변환
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # 첫 프레임이거나 페이지 전환 감지
            is_page_change = False
            
            if prev_frame is None:
                # 첫 프레임
                is_page_change = True
            else:
                # 페이지 전환 감지
                if use_ssim:
                    similarity = _calculate_ssim(prev_frame, gray)
                    is_page_change = similarity < (1.0 - page_change_threshold)
                else:
                    diff_ratio = _calculate_frame_diff(prev_frame, gray)
                    is_page_change = diff_ratio > page_change_threshold
            
            if is_page_change:
                # 이전 페이지의 대표 프레임 선택 (중간 프레임)
                if current_page_frames:
                    mid_idx = len(current_page_frames) // 2
                    rep_frame_idx, rep_frame = current_page_frames[mid_idx]
                    
                    key_frames.append({
                        "frame_index": rep_frame_idx,
                        "timestamp": rep_frame_idx / fps if fps > 0 else 0,
                        "page_number": len(key_frames) + 1,
                    })
                    
                    logger.debug(
                        "Page %d detected: frame_idx=%d, frames_in_page=%d",
                        len(key_frames),
                        rep_frame_idx,
                        len(current_page_frames),
                    )
                
                # 최대 페이지 수 체크
                if len(key_frames) >= max_pages:
                    logger.warning("Max pages reached: %d", max_pages)
                    break
                
                # 새 페이지 시작
                current_page_frames = [(frame_idx, gray)]
            else:
                # 같은 페이지에 프레임 추가
                current_page_frames.append((frame_idx, gray))
            
            prev_frame = gray
            frame_idx += 1
        
        # 마지막 페이지 처리
        if current_page_frames and len(key_frames) < max_pages:
            mid_idx = len(current_page_frames) // 2
            rep_frame_idx, rep_frame = current_page_frames[mid_idx]
            
            key_frames.append({
                "frame_index": rep_frame_idx,
                "timestamp": rep_frame_idx / fps if fps > 0 else 0,
                "page_number": len(key_frames) + 1,
            })
        
        processing_time = time.time() - start_time
        
        return {
            "total_frames": total_frames,
            "video_fps": fps,
            "duration": duration,
            "key_frames": key_frames,
            "pages_detected": len(key_frames),
            "processing_time": processing_time,
            "frame_interval": frame_interval,
        }
        
    finally:
        cap.release()


def extract_frame_at_index(
    video_path: str,
    frame_index: int,
) -> Optional[np.ndarray]:
    """
    특정 인덱스의 프레임 추출
    
    Args:
        video_path: 비디오 파일 경로
        frame_index: 프레임 인덱스
        
    Returns:
        np.ndarray: 프레임 이미지 (BGR) 또는 None
    """
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None
    
    try:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ret, frame = cap.read()
        return frame if ret else None
    finally:
        cap.release()
