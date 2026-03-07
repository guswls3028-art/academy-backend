# PATH: apps/domains/clinic/color_utils.py
"""클리닉 패스카드 색상: 매일 자동 3색 등."""

import hashlib
from django.utils import timezone

# 매일 자동 3색용 팔레트 (날짜별로 동일한 3가지 색상 반환)
CLINIC_DAILY_PALETTE = [
    "#ef4444", "#3b82f6", "#22c55e", "#f97316", "#a855f7", "#ec4899",
    "#eab308", "#06b6d4", "#84cc16", "#f43f5e", "#6366f1", "#14b8a6",
]

DEFAULT_CLINIC_COLORS = ["#ef4444", "#3b82f6", "#22c55e"]


def get_daily_random_colors(date):
    """날짜( date 객체 또는 YYYY-MM-DD 문자열) 기준으로 결정론적 3색 반환."""
    date_str = date.isoformat() if hasattr(date, "isoformat") else str(date)
    h = hashlib.sha256(date_str.encode()).hexdigest()
    n = len(CLINIC_DAILY_PALETTE)
    indices = []
    seen = set()
    for i in range(6):
        idx = int(h[i * 2 : i * 2 + 2], 16) % n
        if idx not in seen:
            seen.add(idx)
            indices.append(idx)
        if len(indices) == 3:
            break
    while len(indices) < 3:
        indices.append(len(indices) % n)
    return [CLINIC_DAILY_PALETTE[i] for i in indices]


def get_effective_clinic_colors(tenant):
    """tenant의 실제 사용 색상 3개 반환 (매일 자동 사용 시 날짜 기준, 아니면 저장값)."""
    if getattr(tenant, "clinic_use_daily_random", False):
        return get_daily_random_colors(timezone.now().date())
    colors = getattr(tenant, "clinic_idcard_colors", None)
    if not colors or not isinstance(colors, list) or len(colors) < 3:
        return DEFAULT_CLINIC_COLORS
    return list(colors[:3])
