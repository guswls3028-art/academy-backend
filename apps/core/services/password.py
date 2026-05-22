# PATH: apps/core/services/password.py
"""
비밀번호 변경 SSOT.
모든 비밀번호 변경 경로(본인 변경, 관리자 리셋, 비밀번호 찾기 등)는
이 모듈의 함수를 통해야 한다. token_version 증가가 보장된다.
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

TEMP_PASSWORD_LENGTH = 6
PENDING_PASSWORD_RESET_TTL_MINUTES = 30


def generate_temp_password(length: int = TEMP_PASSWORD_LENGTH) -> str:
    """
    임시 비밀번호 생성 SSOT.

    자동 발급 비밀번호는 알림톡을 보고 직접 입력하는 일이 많아서
    6자리 숫자형 1회용 비밀번호로 통일한다.
    """
    import secrets
    import string

    chars = string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def change_password(user, new_password: str) -> None:
    """
    비밀번호를 변경하고 token_version을 증가시킨다.
    - set_password + token_version += 1 + save (atomic)
    - 호출자는 old_password 검증을 미리 수행해야 한다.
    """
    user.set_password(new_password)
    user.token_version = (getattr(user, "token_version", 0) or 0) + 1
    user.must_change_password = False
    user.save(update_fields=["password", "token_version", "must_change_password"])


def force_reset_password(user, new_password: str) -> None:
    """
    관리자에 의한 강제 임시 비밀번호 리셋.

    임시 비번은 정의상 1회용이므로 must_change_password=True 강제 설정.
    MustChangePasswordGate 가 첫 로그인 후 비번 변경 외 모든 요청 차단.
    """
    user.set_password(new_password)
    user.token_version = (getattr(user, "token_version", 0) or 0) + 1
    user.must_change_password = True
    user.save(update_fields=["password", "token_version", "must_change_password"])


def rollback_password(
    user,
    old_password_hash: str,
    *,
    must_change_password: bool | None = None,
) -> None:
    """
    비밀번호 변경 후 알림톡 발송 실패 등으로 롤백할 때 사용.
    token_version은 롤백하지 않는다 (이미 변경된 토큰은 무효화 유지).
    """
    user.password = old_password_hash
    update_fields = ["password"]
    if must_change_password is not None:
        user.must_change_password = must_change_password
        update_fields.append("must_change_password")
    user.save(update_fields=update_fields)


def create_pending_password_reset(
    user,
    raw_password: str,
    *,
    ttl_minutes: int = PENDING_PASSWORD_RESET_TTL_MINUTES,
):
    """
    Store a delivered temporary password without changing the active password.

    Public account recovery uses this so async delivery failures cannot lock a
    family out of an account whose old password still worked.
    """
    from apps.core.models import PendingPasswordReset

    expires_at = timezone.now() + timedelta(minutes=ttl_minutes)
    pending, _created = PendingPasswordReset.objects.update_or_create(
        user=user,
        defaults={
            "tenant_id": user.tenant_id,
            "password_hash": make_password(raw_password),
            "expires_at": expires_at,
        },
    )
    return pending


def clear_pending_password_reset(user) -> None:
    """Remove any public recovery temporary password for a user."""
    from apps.core.models import PendingPasswordReset

    PendingPasswordReset.objects.filter(user=user).delete()


def snapshot_pending_password_reset(user) -> dict[str, object] | None:
    """Capture the current pending reset so a failed delivery can restore it."""
    from apps.core.models import PendingPasswordReset

    pending = (
        PendingPasswordReset.objects
        .filter(user=user)
        .order_by("-created_at")
        .values("password_hash", "expires_at")
        .first()
    )
    return dict(pending) if pending else None


def restore_pending_password_reset(user, snapshot: dict[str, object] | None) -> None:
    """Restore a pending reset snapshot, or clear pending state when absent."""
    from apps.core.models import PendingPasswordReset

    if snapshot is None:
        PendingPasswordReset.objects.filter(user=user).delete()
        return

    PendingPasswordReset.objects.update_or_create(
        user=user,
        defaults={
            "tenant_id": user.tenant_id,
            "password_hash": snapshot["password_hash"],
            "expires_at": snapshot["expires_at"],
        },
    )


def consume_pending_password_reset(user, raw_password: str) -> bool:
    """
    Activate a pending temporary password when it is used at login.

    Returns True only when the pending password is valid and has been promoted
    to the real password with must_change_password=True.
    """
    from apps.core.models import PendingPasswordReset

    pending = PendingPasswordReset.objects.filter(user=user).order_by("-created_at").first()
    if not pending:
        return False

    if pending.expires_at <= timezone.now():
        pending.delete()
        return False

    if not check_password(raw_password, pending.password_hash):
        return False

    force_reset_password(user, raw_password)
    pending.delete()
    return True


def pending_password_reset_matches(user, raw_password: str) -> bool:
    """
    Check a pending temporary password without promoting it.

    Login uses this before mutating password state so inactive/non-loginable
    accounts cannot consume a pending reset while still being rejected.
    """
    from apps.core.models import PendingPasswordReset

    pending = PendingPasswordReset.objects.filter(user=user).order_by("-created_at").first()
    if not pending:
        return False

    if pending.expires_at <= timezone.now():
        pending.delete()
        return False

    return check_password(raw_password, pending.password_hash)
