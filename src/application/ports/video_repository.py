"""
Video Repository Port (인터페이스)

DB 상태 업데이트: mark_processing, complete_video, fail_video
Worker는 이 포트를 통해서만 Video 상태를 변경.
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
    ) -> tuple[bool, str]:
        """비디오 처리 완료 (READY 상태로 전환)"""
        pass

    @abstractmethod
    def fail_video(self, video_id: int, reason: str) -> tuple[bool, str]:
        """비디오 처리 실패 (FAILED 상태로 전환)"""
        pass
