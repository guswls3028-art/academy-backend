"""Tenant-owned, independent initial-password rules for new family accounts."""
from __future__ import annotations

from apps.core.services.account_credentials import protect_password, recover_password
from apps.core.services.password import generate_temp_password

MODES = ("phone_last4", "fixed", "random")


def password_settings(tenant) -> dict:
    stored = tenant.account_password_policy or {}
    result = {}
    for role in ("student", "parent"):
        result[f"{role}_mode"] = stored.get(f"{role}_mode", "phone_last4")
        result[f"{role}_fixed_password"] = recover_password(
            stored.get(f"{role}_fixed_password", ""), context=f"policy:{tenant.pk}:{role}"
        )
    return result


def save_password_settings(tenant, data: dict) -> dict:
    policy = password_settings(tenant)
    unknown = set(data) - set(policy)
    if unknown:
        raise ValueError("지원하지 않는 비밀번호 설정입니다.")
    policy.update(data)
    stored = {}
    for role in ("student", "parent"):
        mode = policy[f"{role}_mode"]
        password = policy[f"{role}_fixed_password"]
        if mode not in MODES:
            raise ValueError("초기 비밀번호 방식이 올바르지 않습니다.")
        if not isinstance(password, str) or (mode == "fixed" and len(password) < 4):
            raise ValueError("공통 초기 비밀번호는 4자 이상 입력해 주세요.")
        stored[f"{role}_mode"] = mode
        stored[f"{role}_fixed_password"] = (
            protect_password(password, context=f"policy:{tenant.pk}:{role}") if password else ""
        )
    tenant.account_password_policy = stored
    tenant.save(update_fields=["account_password_policy"])
    return password_settings(tenant)


def initial_password(tenant, *, role: str, phone: str | None, supplied: str | None = None) -> str:
    if supplied:
        if len(supplied) < 4:
            raise ValueError("초기 비밀번호는 4자 이상 입력해 주세요.")
        return supplied
    policy = password_settings(tenant)
    mode = policy[f"{role}_mode"]
    if mode == "fixed":
        password = policy[f"{role}_fixed_password"]
        if len(password) < 4:
            raise ValueError("학원의 공통 초기 비밀번호 설정을 확인해 주세요.")
        return password
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if mode == "phone_last4" and len(digits) == 11:
        return digits[-4:]
    return generate_temp_password()
