# apps/worker/ai/pipelines/homework_video_analyzer.py
from __future__ import annotations

from typing import Dict, Any, List
import cv2  # type: ignore
import numpy as np  # type: ignore

from apps.worker.ai_worker.ai.pipelines.video_frame_extractor import (
    extract_key_frames,
    extract_frame_at_index,
)
from apps.worker.ai_worker.ai.utils.image_resizer import resize_if_large


def _estimate_writing_score(gray_roi: np.ndarray) -> float:
    if gray_roi.size == 0:
        return 0.0
    dark = (gray_roi < 220).sum()
    total = gray_roi.size
    return float(dark) / float(total)


def analyze_homework_video(
    video_path: str,
    frame_stride: int = 10,
    min_frame_count: int = 30,
    use_key_frames: bool = True,  # 키 프레임 추출 사용 여부
    max_pages: int = 10,  # 최대 페이지 수
    processing_timeout: int = 60,  # 처리 타임아웃 (초)
) -> Dict[str, Any]:
    # 키 프레임 추출 사용 (권장)
    if use_key_frames:
        try:
            key_frames_info = extract_key_frames(
                video_path=video_path,
                target_fps=2.0,  # 1-3 fps 권장
                max_pages=max_pages,
                processing_timeout=processing_timeout,
            )
            
            # 키 프레임에서만 분석
            frame_results: List[Dict[str, Any]] = []
            
            for key_frame in key_frames_info["key_frames"]:
                frame_idx = key_frame["frame_index"]
                frame = extract_frame_at_index(video_path, frame_idx)
                
                if frame is None:
                    continue
                
                # 이미지 리사이징 (대용량 처리 전)
                frame_resized, was_resized = resize_if_large(frame, max_megapixels=4.0)
                gray = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2GRAY)
                score = _estimate_writing_score(gray)
                
                frame_results.append({
                    "index": frame_idx,
                    "timestamp": key_frame["timestamp"],
                    "page_number": key_frame["page_number"],
                    "writing_score": round(float(score), 4),
                    "has_writing": bool(score >= 0.05),
                    "was_resized": was_resized,
                })
            
            sampled = len(frame_results)
            if sampled == 0:
                return {
                    "total_frames": key_frames_info["total_frames"],
                    "sampled_frames": 0,
                    "avg_writing_score": 0.0,
                    "filled_ratio": 0.0,
                    "frames": [],
                    "pages_detected": 0,
                    "too_short": key_frames_info["total_frames"] < min_frame_count,
                }
            
            avg = sum(fr["writing_score"] for fr in frame_results) / sampled
            filled = sum(1 for fr in frame_results if fr["has_writing"])
            ratio = filled / sampled
            
            return {
                "total_frames": key_frames_info["total_frames"],
                "sampled_frames": sampled,
                "pages_detected": key_frames_info["pages_detected"],
                "avg_writing_score": round(float(avg), 4),
                "filled_ratio": round(float(ratio), 4),
                "frames": frame_results,
                "too_short": key_frames_info["total_frames"] < min_frame_count,
                "key_frames_extraction": True,
            }
        except Exception as e:
            # 키 프레임 추출 실패 시 레거시 방식으로 fallback
            import logging
            logger = logging.getLogger(__name__)
            logger.warning("Key frame extraction failed, using legacy method: %s", e)
    
    # 레거시 방식 (전체 프레임 샘플링)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {video_path}")

    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frame_idx = 0
    frame_results: List[Dict[str, Any]] = []

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx % frame_stride != 0:
            frame_idx += 1
            continue

        # 이미지 리사이징 (대용량 처리 전)
        frame_resized, was_resized = resize_if_large(frame, max_megapixels=4.0)
        gray = cv2.cvtColor(frame_resized, cv2.COLOR_BGR2GRAY)
        score = _estimate_writing_score(gray)

        frame_results.append(
            {
                "index": frame_idx,
                "writing_score": round(float(score), 4),
                "has_writing": bool(score >= 0.05),
                "was_resized": was_resized,
            }
        )
        frame_idx += 1

    cap.release()

    sampled = len(frame_results)
    if sampled == 0:
        return {
            "total_frames": total_frames,
            "sampled_frames": 0,
            "avg_writing_score": 0.0,
            "filled_ratio": 0.0,
            "frames": [],
            "too_short": total_frames < min_frame_count,
            "key_frames_extraction": False,
        }

    avg = sum(fr["writing_score"] for fr in frame_results) / sampled
    filled = sum(1 for fr in frame_results if fr["has_writing"])
    ratio = filled / sampled

    return {
        "total_frames": total_frames,
        "sampled_frames": sampled,
        "avg_writing_score": round(float(avg), 4),
        "filled_ratio": round(float(ratio), 4),
        "frames": frame_results,
        "too_short": total_frames < min_frame_count,
        "key_frames_extraction": False,
    }
