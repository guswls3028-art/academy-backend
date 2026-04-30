"""In-process circuit breaker — 외부 API 사고 폭주 차단.

설계:
- per-process state (multi-instance ASG에서는 인스턴스별 독립 상태).
- 외부 라이브러리 추가 X (pybreaker 등 의존성 회피).
- state: closed → open(시간) → half_open → closed/open
- open 진입 시 OpsAuditLog에 1회 기록 (check_dev_alerts에서 알림).

사용:
    from apps.shared.utils.circuit_breaker import circuit_breaker, CircuitOpenError

    @circuit_breaker(name="solapi_send", failure_threshold=5, window_seconds=30, cooldown_seconds=60)
    def call_solapi(...): ...

    try:
        call_solapi(...)
    except CircuitOpenError:
        # 즉시 fallback (예: SQS DLQ 라우팅, 사용자 안내 메시지)
        ...

기본값:
- 30초 윈도우 내 5회 실패 → open.
- open 후 60초 대기 → half_open.
- half_open에서 1회 성공 → closed. 1회 실패 → open(2배 cooldown).
- success는 윈도우 내 카운트 리셋(연속 실패만 의미 있게).

재진입 안전: threading.RLock으로 카운트/state 갱신 동시성 보호.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from functools import wraps
from typing import Callable, Iterable, Optional, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., object])


class CircuitOpenError(RuntimeError):
    """Circuit이 open 상태 — 호출 즉시 차단."""

    def __init__(self, name: str, retry_after: float):
        self.name = name
        self.retry_after = retry_after
        super().__init__(f"Circuit '{name}' is open (retry_after={retry_after:.1f}s)")


@dataclass
class _State:
    failures: deque = field(default_factory=deque)
    state: str = "closed"  # closed | open | half_open
    open_until: float = 0.0
    cooldown_seconds: float = 60.0
    consecutive_open_count: int = 0  # 연속 open 횟수 — exponential backoff용
    lock: threading.RLock = field(default_factory=threading.RLock)


_STATES: dict[str, _State] = {}
_STATES_LOCK = threading.Lock()


def _get_state(name: str, default_cooldown: float) -> _State:
    with _STATES_LOCK:
        st = _STATES.get(name)
        if st is None:
            st = _State(cooldown_seconds=default_cooldown)
            _STATES[name] = st
        return st


def _record_open_audit(name: str, failure_count: int) -> None:
    """Open 진입 시 OpsAuditLog 기록. 알림 룰이 이걸 읽는다."""
    try:
        from apps.core.models import OpsAuditLog
        OpsAuditLog.objects.create(
            actor_username="system",
            action="circuit.open",
            summary=f"{name} (failures={failure_count})",
            result=OpsAuditLog.Result.FAILED,
        )
    except Exception:
        # DB unavailable이거나 importable 안 되는 (테스트 환경 등) → silent
        logger.warning("circuit.open audit log failed for %s", name, exc_info=False)


def circuit_breaker(
    *,
    name: str,
    failure_threshold: int = 5,
    window_seconds: float = 30.0,
    cooldown_seconds: float = 60.0,
    expected_exceptions: Optional[Iterable[type[BaseException]]] = None,
) -> Callable[[F], F]:
    """외부 호출에 circuit breaker 적용.

    expected_exceptions: 이 예외들만 failure로 카운트. 다른 예외(코드 버그 등)는 통과.
                        None이면 모든 Exception이 failure (단 CircuitOpenError 자체는 제외).
    """
    expected_tuple = tuple(expected_exceptions) if expected_exceptions else (Exception,)

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            st = _get_state(name, cooldown_seconds)
            now = time.monotonic()

            with st.lock:
                if st.state == "open":
                    if now < st.open_until:
                        raise CircuitOpenError(name, st.open_until - now)
                    # cooldown 끝 → half_open
                    st.state = "half_open"
                    logger.info("circuit '%s': half_open (probe)", name)

            try:
                result = fn(*args, **kwargs)
            except CircuitOpenError:
                raise
            except expected_tuple as e:
                with st.lock:
                    if st.state == "half_open":
                        # half_open 실패 → 다시 open + exponential backoff (max 8x)
                        st.consecutive_open_count = min(st.consecutive_open_count + 1, 4)
                        st.open_until = now + cooldown_seconds * (2 ** (st.consecutive_open_count - 1))
                        st.state = "open"
                        logger.warning(
                            "circuit '%s': re-opened (half_open probe failed, cooldown=%.1fs)",
                            name, st.open_until - now,
                        )
                        _record_open_audit(name, len(st.failures) + 1)
                        raise

                    # closed → failure 카운트
                    st.failures.append(now)
                    cutoff = now - window_seconds
                    while st.failures and st.failures[0] < cutoff:
                        st.failures.popleft()
                    if len(st.failures) >= failure_threshold:
                        st.state = "open"
                        st.consecutive_open_count = 1
                        st.open_until = now + cooldown_seconds
                        logger.warning(
                            "circuit '%s': OPEN (failures=%d in %.0fs, cooldown=%.1fs): %s",
                            name, len(st.failures), window_seconds, cooldown_seconds, e,
                        )
                        _record_open_audit(name, len(st.failures))
                raise

            # 성공 — half_open 이면 close
            with st.lock:
                if st.state == "half_open":
                    st.state = "closed"
                    st.failures.clear()
                    st.consecutive_open_count = 0
                    logger.info("circuit '%s': closed (probe success)", name)
                else:
                    # closed에서 성공: 윈도우 내 실패 기록 비움 (연속 실패만 의미 있게).
                    st.failures.clear()
            return result

        return wrapper  # type: ignore[return-value]

    return decorator


def reset_circuit(name: Optional[str] = None) -> None:
    """테스트/수동 복구용 — 특정 circuit 또는 전체 reset."""
    with _STATES_LOCK:
        if name is None:
            _STATES.clear()
        else:
            _STATES.pop(name, None)


def get_circuit_state(name: str) -> dict:
    """현재 circuit 상태 조회 (운영 디버깅 / health endpoint 용)."""
    with _STATES_LOCK:
        st = _STATES.get(name)
    if st is None:
        return {"state": "closed", "failures": 0, "open_until": 0.0}
    with st.lock:
        return {
            "state": st.state,
            "failures": len(st.failures),
            "open_until": st.open_until,
            "consecutive_open_count": st.consecutive_open_count,
        }
