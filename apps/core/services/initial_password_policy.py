"""Tenant-owned initial-password policy; no implicit registration choice."""
from __future__ import annotations

from django.db import transaction

from apps.core.services.account_credentials import protect_password, recover_password
from apps.core.services.password import generate_temp_password

MODES = ("phone_last4", "fixed", "random")


def password_settings(tenant) -> dict:
    stored = tenant.account_password_policy or {}
    result = {}
    for role in ("student", "parent"):
        result[f"{role}_mode"] = stored.get(f"{role}_mode")
        result[f"{role}_fixed_password"] = recover_password(
            stored.get(f"{role}_fixed_password", ""), context=f"policy:{tenant.pk}:{role}"
        )
    return result


@transaction.atomic
def save_password_settings(tenant, data: dict) -> dict:
    from apps.core.models import Tenant

    current = Tenant.objects.select_for_update().get(pk=tenant.pk)
    policy = password_settings(current)
    unknown = set(data) - set(policy)
    if unknown:
        raise ValueError("지원하지 않는 비밀번호 설정입니다.")
    previous = dict(policy)
    policy.update(data)
    stored = dict(current.account_password_policy or {})
    for role in ("student", "parent"):
        mode_key, password_key = f"{role}_mode", f"{role}_fixed_password"
        mode, password = policy[mode_key], policy[password_key]
        if (mode_key in data or mode is not None) and mode not in MODES:
            raise ValueError("초기 비밀번호 방식을 선택해 주세요.")
        if not isinstance(password, str) or (mode == "fixed" and len(password) < 4):
            raise ValueError("직접 입력 비밀번호는 4자 이상 입력해 주세요.")
        if password and mode is None:
            raise ValueError("초기 비밀번호 방식을 먼저 선택해 주세요.")
        if mode_key in data:
            stored[mode_key] = mode
        if password_key in data and (password != previous[password_key] or password_key not in stored):
            stored[password_key] = protect_password(password, context=f"policy:{tenant.pk}:{role}") if password else ""
    if stored != current.account_password_policy:
        current.account_password_policy = stored
        current.save(update_fields=["account_password_policy"])
    tenant.account_password_policy = stored
    return password_settings(current)


def initial_password(tenant, *, role: str, phone: str | None, supplied: str | None = None, mode: str | None = None) -> str:
    if role not in ("student", "parent"):
        raise ValueError("초기 비밀번호 대상이 올바르지 않습니다.")
    explicit_mode = mode is not None
    if supplied and (not explicit_mode or mode == "fixed"):
        if len(supplied) < 4:
            raise ValueError("초기 비밀번호는 4자 이상 입력해 주세요.")
        return supplied
    if supplied:
        raise ValueError("선택한 방식과 직접 입력 비밀번호가 일치하지 않습니다.")
    policy = password_settings(tenant)
    if not explicit_mode:
        mode = policy[f"{role}_mode"]
    if mode not in MODES:
        raise ValueError(f"{'학생' if role == 'student' else '학부모'} 초기 비밀번호 방식을 선택해 주세요.")
    if mode == "fixed":
        password = "" if explicit_mode else policy[f"{role}_fixed_password"]
        if len(password) < 4:
            raise ValueError("직접 입력 비밀번호는 4자 이상 입력해 주세요.")
        return password
    if mode == "random":
        return generate_temp_password()
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if len(digits) != 11 or not digits.startswith("010"):
        raise ValueError(f"{'학생' if role == 'student' else '학부모'} 본인 전화번호가 없어 뒤 4자리 방식을 사용할 수 없습니다. 직접 입력 또는 랜덤 번호를 선택해 주세요.")
    return digits[-4:]
