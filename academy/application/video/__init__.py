"""
academy.application.video — 영상 인코딩 application layer.

현재는 cancellation 신호(CancelledError) 한 개만 노출. 인코딩 본 경로는
worker entry(`apps/worker/video_worker/batch_main.py`) → adapter 직호출
(`academy/adapters/video/processor.py`) 구조라 application handler 객체는
없다. (이전 SQS daemon 시절의 ProcessVideoJobHandler 는 2026-05-10 batch-only
컷오버 시점에 폐기.)
"""
from academy.application.video.cancellation import CancelledError

__all__ = ["CancelledError"]
