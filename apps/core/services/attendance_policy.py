# PATH: apps/core/services/attendance_policy.py
from __future__ import annotations

from datetime import datetime, time
from typing import Optional, Union

from apps.core.models import Program, Tenant


TimeLike = Union[str, time]


def _to_time(v: TimeLike) -> Optional[time]:
    if v is None:
        return None
    if isinstance(v, time):
        return v

    s = str(v or "").strip()
    if not s:
        return None

    # "HH:MM" or "HH:MM:SS"
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).time()
        except Exception:
            continue
    return None


def calculate_duration_hours(start: TimeLike, end: TimeLike) -> float:
    """
    - 날짜 없이 시간만 들어오는 근태 입력 전제
    - end < start 인 경우(야간 근무)까지는 정책 정의가 필요하므로 기본은 0 처리
    """
    st = _to_time(start)
    et = _to_time(end)
    if not st or not et:
        return 0.0

    start_dt = datetime(2000, 1, 1, st.hour, st.minute, st.second)
    end_dt = datetime(2000, 1, 1, et.hour, et.minute, et.second)

    if end_dt < start_dt:
        return 0.0

    seconds = (end_dt - start_dt).total_seconds()
    if seconds <= 0:
        return 0.0

    return float(seconds / 3600.0)


def get_hourly_rate_for_tenant(tenant: Tenant) -> int:
    """
    Enterprise 정책:
    - 시급은 Program.feature_flags['attendance_hourly_rate'] 로 제공
    - 없으면 15000 기본값
    """
    from academy.adapters.db.django import repositories_core as core_repo
    program = core_repo.program_get_by_tenant_only_feature_flags(tenant)
    flags = getattr(program, "feature_flags", {}) or {}
    try:
        v = int(flags.get("attendance_hourly_rate", 15000))
        return v if v > 0 else 15000
    except Exception:
        return 15000


def calculate_amount(tenant: Tenant, duration_hours: float) -> int:
    hourly = get_hourly_rate_for_tenant(tenant)
    if duration_hours <= 0:
        return 0
    return int(duration_hours * hourly)
