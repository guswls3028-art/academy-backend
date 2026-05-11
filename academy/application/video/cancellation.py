"""
영상 인코딩 도중 취소 요청을 알리는 control-flow 예외.

- API/admin 측에서 cancel_requested = True 로 마킹하거나
- batch_main 이 SIGTERM/SIGINT 수신 후 _shutdown_event.set() 한 경우
adapter(processor / transcoder) 단계마다 `_check_abort` 또는 `cancel_event` 가
이 예외를 raise. worker entry 가 잡아 재시도 로직 없이 ACK 후 종료.

DB fail_video 호출 없이 스킵해야 하므로 RuntimeError 와 구분되는 별도 타입.
"""
from __future__ import annotations


class CancelledError(RuntimeError):
    pass
