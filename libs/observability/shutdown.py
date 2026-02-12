"""
Graceful Shutdown 유틸리티

SIGTERM, SIGINT 신호를 처리하여 안전하게 종료
"""

import signal
import logging
import sys
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_shutdown_handlers = []
_shutdown_requested = False


def register_shutdown_handler(handler: Callable[[], None]) -> None:
    """종료 핸들러 등록"""
    _shutdown_handlers.append(handler)


def is_shutdown_requested() -> bool:
    """종료 요청 여부 확인"""
    return _shutdown_requested


def _signal_handler(signum, frame):
    """시그널 핸들러"""
    global _shutdown_requested
    signal_name = signal.Signals(signum).name
    logger.info(f"Received {signal_name}, initiating graceful shutdown...")
    _shutdown_requested = True
    
    # 등록된 핸들러 실행
    for handler in _shutdown_handlers:
        try:
            handler()
        except Exception as e:
            logger.error(f"Error in shutdown handler: {e}")
    
    logger.info("Graceful shutdown complete")
    sys.exit(0)


def setup_graceful_shutdown() -> None:
    """Graceful shutdown 설정"""
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    logger.info("Graceful shutdown handlers registered")
