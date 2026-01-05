# apps/worker/ai/pipelines/homework_video_analyzer.py
from __future__ import annotations

from typing import Dict, Any, List
import cv2  # type: ignore
import numpy as np  # type: ignore


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
) -> Dict[str, Any]:
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

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        score = _estimate_writing_score(gray)

        frame_results.append(
            {
                "index": frame_idx,
                "writing_score": round(float(score), 4),
                "has_writing": bool(score >= 0.05),
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
    }
