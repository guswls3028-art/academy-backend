# PATH: apps/core/services/password.py
"""
비밀번호 변경 SSOT.
모든 비밀번호 변경 경로(본인 변경, 관리자 리셋, 비밀번호 찾기 등)는
이 모듈의 함수를 통해야 한다. token_version 증가가 보장된다.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Callable

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

TEMP_PASSWORD_LENGTH = 6
PENDING_PASSWORD_RESET_TTL_MINUTES = 30


class CurrentPasswordMismatch(ValueError):
    """The supplied current password no longer matches the locked user row."""


class PasswordNoticeDeliveryError(RuntimeError):
    """The password change could not be committed with its required notice."""


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


@transaction.atomic
def change_password(user, new_password: str) -> None:
    """
    비밀번호를 변경하고 token_version을 증가시킨다.
    - set_password + token_version += 1 + save (atomic)
    - 호출자는 old_password 검증을 미리 수행해야 한다.
    """
    locked_user = get_user_model().objects.select_for_update().get(pk=user.pk)
    locked_user.set_password(new_password)
    locked_user.token_version = (getattr(locked_user, "token_version", 0) or 0) + 1
    locked_user.must_change_password = False
    locked_user.save(update_fields=["password", "token_version", "must_change_password"])
    clear_pending_password_reset(locked_user)
    for field in ("password", "token_version", "must_change_password"):
        setattr(user, field, getattr(locked_user, field))


def change_password_with_notice(
    user,
    *,
    current_password: str,
    new_password: str,
    send_notice: Callable[..., bool],
):
    """Atomically verify, change, invalidate tokens, and reserve the notice.

    The current-password check is deliberately repeated after ``select_for_update``.
    Without that lock, two concurrent requests can both validate the same old
    password and the last writer silently wins.  Raising on notice failure also
    rolls back ``password``, ``token_version``, ``must_change_password`` and the
    durable notification reservation together.
    """

    User = get_user_model()
    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(pk=user.pk)
        if not locked_user.check_password(current_password):
            raise CurrentPasswordMismatch("현재 비밀번호가 올바르지 않습니다.")

        change_password(locked_user, new_password)
        if not send_notice(user=locked_user, password=str(new_password)):
            raise PasswordNoticeDeliveryError(
                "비밀번호 변경 알림톡 발송에 실패했습니다. 잠시 후 다시 시도해 주세요."
            )

    for field in ("password", "token_version", "must_change_password"):
        setattr(user, field, getattr(locked_user, field))
    return locked_user


@transaction.atomic
def force_reset_password(user, new_password: str) -> None:
    """
    관리자에 의한 강제 임시 비밀번호 리셋.

    임시 비밀번호이므로 must_change_password=True로 변경 권장 상태를 표시한다.
    이 플래그는 로그인이나 다른 API 사용을 차단하지 않는다.
    """
    locked_user = get_user_model().objects.select_for_update().get(pk=user.pk)
    locked_user.set_password(new_password)
    locked_user.token_version = (getattr(locked_user, "token_version", 0) or 0) + 1
    locked_user.must_change_password = True
    locked_user.save(update_fields=["password", "token_version", "must_change_password"])
    clear_pending_password_reset(locked_user)
    for field in ("password", "token_version", "must_change_password"):
        setattr(user, field, getattr(locked_user, field))


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


def consume_pending_password_reset(user, raw_password: str) -> bool:
    """
    Activate a pending temporary password when it is used at login.

    Returns True only when the pending password is valid and has been promoted
    to the real password with must_change_password=True.
    """
    from apps.core.models import PendingPasswordReset

    User = get_user_model()
    with transaction.atomic():
        locked_user = User.objects.select_for_update().get(pk=user.pk)

        # A staff/owner reset may have completed after login candidate lookup.
        # Treat the now-current password as valid without consuming a newer,
        # unrelated pending reset.
        if locked_user.check_password(raw_password):
            for field in ("password", "token_version", "must_change_password"):
                setattr(user, field, getattr(locked_user, field))
            return True

        pending = (
            PendingPasswordReset.objects.select_for_update()
            .filter(user=locked_user)
            .order_by("-created_at")
            .first()
        )
        if not pending:
            return False

        if pending.expires_at <= timezone.now():
            pending.delete()
            return False

        if not check_password(raw_password, pending.password_hash):
            return False

        force_reset_password(locked_user, raw_password)
        for field in ("password", "token_version", "must_change_password"):
            setattr(user, field, getattr(locked_user, field))
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
