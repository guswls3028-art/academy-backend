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

    def try_claim_video(
        self, video_id: int, worker_id: str, lease_seconds: int = 14400
    ) -> bool:
        """
        UPLOADED → PROCESSING 원자 변경 + leased_by, leased_until 설정.
        이미 PROCESSING/READY면 False. 빠른 ACK + DB lease 패턴용.
        기본 구현: mark_processing 호출 (호환용).
        """
        return self.mark_processing(video_id)

    def try_reclaim_video(self, video_id: int) -> bool:
        """
        PROCESSING 이지만 leased_until < now 인 경우 UPLOADED로 되돌림.
        기본 구현: False (구현체에서 override).
        """
        return False

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
