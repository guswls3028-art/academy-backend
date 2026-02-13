"""
Pre-Validation Layer (설계 2.3 반영)

Lite/Basic에서 "실패 없음"을 위한 거부 정책.
거부 사유(rejection_code)는 프론트에서 사용자 안내 문구로 노출 가능.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# 거부 코드 (운영 문장 매핑용, 설계 문서와 일치)
REJECTION_CODES = {
    "RESOLUTION_TOO_LOW": "해상도가 낮습니다. 더 선명하게 촬영해 주세요.",
    "FILE_TOO_LARGE": "파일 크기가 제한을 초과했습니다.",
    "VIDEO_TOO_LONG": "동영상 길이 제한을 초과했습니다.",
    "BLUR_OR_SHAKE": "흔들리거나 흐릿합니다. 고정해서 다시 촬영해 주세요.",
    "TOO_DARK": "너무 어둡습니다. 밝은 곳에서 촬영해 주세요.",
    "INVALID_FORMAT": "지원하지 않는 파일 형식입니다.",
    "OMR_PHOTO_NOT_ALLOWED": (
        "Basic 요금제에서는 스캔된 OMR만 가능합니다. 촬영물은 Premium에서 이용해 주세요."
    ),
}

# job_type별 파일 크기 상한 (MB)
MAX_FILE_SIZE_MB = {
    "omr_grading": 50,
    "essay_answer_extraction": 50,
    "homework_photo_analysis": 20,
    "homework_video_analysis": 500,
}

# 동영상 길이 상한 (초)
MAX_VIDEO_DURATION_SEC = 600

# 해상도 최소 (짧은 변 px)
MIN_RESOLUTION_SHORT_SIDE = 600

# 허용 이미지 포맷
ALLOWED_IMAGE_FORMATS = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
ALLOWED_VIDEO_FORMATS = {"video/mp4", "video/quicktime"}


def validate_input_for_basic(
    tier: str,
    job_type: str,
    payload: dict,
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Lite/Basic 입력 품질 게이트.

    Returns:
        (ok, error_message, rejection_code)
        - ok: True면 통과, False면 거부
        - error_message: 사용자/로그용 메시지 (REJECTION_CODES 값 또는 커스텀)
        - rejection_code: 프론트 매핑용 (REJECTION_CODES 키)
    """
    tier = (tier or "").lower()
    job_type = (job_type or "").lower()

    # Basic에서 OMR 촬영물 거부 (mode=photo/video)
    if tier in ("lite", "basic") and job_type == "omr_grading":
        mode = (payload.get("mode") or "").lower()
        if mode in ("photo", "video"):
            return False, REJECTION_CODES["OMR_PHOTO_NOT_ALLOWED"], "OMR_PHOTO_NOT_ALLOWED"

    # 파일 크기 (payload에 file_size_bytes 또는 file_size_mb 있으면 검사)
    size_mb = payload.get("file_size_mb")
    if size_mb is None and payload.get("file_size_bytes") is not None:
        try:
            size_mb = int(payload["file_size_bytes"]) / (1024 * 1024)
        except (TypeError, ValueError):
            size_mb = None
    if size_mb is not None:
        max_mb = MAX_FILE_SIZE_MB.get(job_type, 50)
        if size_mb > max_mb:
            return False, REJECTION_CODES["FILE_TOO_LARGE"], "FILE_TOO_LARGE"

    # 동영상 길이 (homework_video_analysis 등)
    if job_type == "homework_video_analysis":
        duration_sec = payload.get("duration_seconds")
        if duration_sec is not None and float(duration_sec) > MAX_VIDEO_DURATION_SEC:
            return False, REJECTION_CODES["VIDEO_TOO_LONG"], "VIDEO_TOO_LONG"

    # content_type / format (헤더에서 올 수 있음)
    content_type = (payload.get("content_type") or payload.get("file_type") or "").strip().lower()
    if content_type:
        allowed = ALLOWED_IMAGE_FORMATS | ALLOWED_VIDEO_FORMATS
        if content_type not in allowed and "/" in content_type:
            # image/*, video/* 부분 일치
            main = content_type.split("/")[0]
            if main not in ("image", "video"):
                return False, REJECTION_CODES["INVALID_FORMAT"], "INVALID_FORMAT"

    return True, None, None


def get_rejection_message(rejection_code: Optional[str]) -> str:
    """rejection_code → 사용자 노출 문구."""
    if not rejection_code:
        return ""
    return REJECTION_CODES.get(rejection_code, "입력 조건을 확인해 주세요.")
