# PATH: apps/worker/video_worker/video/processor.py

from __future__ import annotations

import logging
from typing import Dict, Any

import requests

from apps.worker.video_worker.video.storage import (
    upload_hls_directory,
    upload_thumbnail_bytes,
)
from apps.support.video.utils import (
    extract_duration_seconds_from_url,
    generate_thumbnail_from_url,
)

logger = logging.getLogger("video.worker.processor")


class VideoProcessor:
    """
    Video processing pipeline (Worker)

    책임:
    - source video URL 처리
    - HLS 변환
    - 썸네일 생성
    - API 서버에 완료/실패 보고
    """

    def __init__(self, *, api_base: str, worker_id: str, worker_token: str):
        self.api_base = api_base.rstrip("/")
        self.worker_id = worker_id
        self.worker_token = worker_token

    # --------------------------------------------------
    # Internal HTTP helpers
    # --------------------------------------------------

    def _headers(self) -> Dict[str, str]:
        return {
            "X-Worker-Token": self.worker_token,
            "X-Worker-Id": self.worker_id,
        }

    def _post(self, path: str, json: Dict[str, Any]) -> requests.Response:
        url = f"{self.api_base}{path}"
        return requests.post(url, json=json, headers=self._headers(), timeout=30)

    # --------------------------------------------------
    # Main entry
    # --------------------------------------------------

    def process(self, *, video_id: int, source_url: str) -> None:
        """
        단일 영상 처리
        """
        try:
            logger.info(
                "video processing started video_id=%s worker=%s",
                video_id,
                self.worker_id,
            )

            # 1. duration 추출
            duration = extract_duration_seconds_from_url(source_url)
            if not duration or duration <= 0:
                raise RuntimeError("duration_probe_failed")

            # 2. HLS 변환
            hls_output_dir = upload_hls_directory(
                video_id=video_id,
                source_url=source_url,
            )

            # 3. 썸네일 생성 (중앙 프레임)
            if duration >= 10:
                ss = int(duration * 0.5)
            elif duration >= 3:
                ss = max(1, duration // 2)
            else:
                ss = 0

            thumbnail_bytes = generate_thumbnail_from_url(
                source_url,
                ss_seconds=ss,
            )

            if thumbnail_bytes:
                upload_thumbnail_bytes(
                    video_id=video_id,
                    data=thumbnail_bytes,
                )
            else:
                logger.warning(
                    "thumbnail generation failed video_id=%s",
                    video_id,
                )

            # 4. 완료 보고
            resp = self._post(
                f"/internal/video-worker/{video_id}/complete/",
                {
                    "hls_path": hls_output_dir,
                    "duration": duration,
                },
            )
            resp.raise_for_status()

            logger.info(
                "video processing completed video_id=%s duration=%s",
                video_id,
                duration,
            )

        except Exception as e:
            logger.exception(
                "video processing failed video_id=%s error=%s",
                video_id,
                e,
            )
            try:
                self._post(
                    f"/internal/video-worker/{video_id}/fail/",
                    {"reason": str(e)},
                )
            except Exception:
                logger.exception(
                    "failed to report processing failure video_id=%s",
                    video_id,
                )


# ==================================================
# ✅ Worker entrypoint (상품 계약)
# - main.py 와의 단일 접점
# - job schema 검증 책임을 여기서 종료
# ==================================================

def process_video_job(*, job: Dict[str, Any], cfg, client) -> None:
    """
    Worker → Processor 어댑터
    """

    video_id = job.get("video_id")
    source_url = job.get("source_url")

    if not video_id or not source_url:
        raise KeyError("video_id or source_url missing in job")

    processor = VideoProcessor(
        api_base=cfg.API_BASE_URL,
        worker_id=cfg.WORKER_ID,
        worker_token=cfg.WORKER_TOKEN,
    )

    processor.process(
        video_id=int(video_id),
        source_url=str(source_url),
    )
