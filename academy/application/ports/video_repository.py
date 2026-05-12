"""
Video Repository Port (인터페이스)

DB 상태 업데이트: mark_processing, complete_video, fail_video.
Worker는 이 포트를 통해서만 Video 상태를 변경.

(이전 SQS daemon 시절의 try_claim_video / try_reclaim_video 는 batch-only
컷오버(2026-05-10) 시점에 함께 폐기.)
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class IVideoRepository(ABC):
    """Video 상태 Repository 추상 인터페이스"""

    @abstractmethod
    def mark_processing(self, video_id: int) -> bool:
        """비디오를 PROCESSING 상태로 변경 (멱등성 보장)"""
        pass

    @abstractmethod
    def complete_video(
        self,
        video_id: int,
        hls_path: str,
        duration: int | None = None,
        thumbnail_r2_key: str | None = None,
    ) -> tuple[bool, str]:
        """비디오 처리 완료 (READY 상태로 전환).

        thumbnail_r2_key: Worker가 R2에 올린 thumbnail.jpg 의 key. 비어 있으면
        모바일 카드 UI에 회색 placeholder 만 보이므로 invariant 상 항상 전달돼야 한다.
        """
        pass

    @abstractmethod
    def fail_video(self, video_id: int, reason: str) -> tuple[bool, str]:
        """비디오 처리 실패 (FAILED 상태로 전환)"""
        pass
