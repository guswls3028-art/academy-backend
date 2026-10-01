"""Protect and resolve the usable passwords sent in account Alimtalk.

Password hashes remain the authentication authority. Encrypted notice values
are never included in ordinary account/student serializers.
"""
from __future__ import annotations

import base64
import hashlib
import json

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.db import transaction


def protect_password(password: str, *, context: str) -> str:
    key = hashlib.sha256(
        f"academy:account-credential:v1:{settings.SECRET_KEY}".encode()
    ).digest()
    value = json.dumps({"context": context, "password": password}, ensure_ascii=False)
    return Fernet(base64.urlsafe_b64encode(key)).encrypt(value.encode()).decode()


def recover_password(ciphertext: str, *, context: str) -> str:
    key = hashlib.sha256(
        f"academy:account-credential:v1:{settings.SECRET_KEY}".encode()
    ).digest()
    try:
        value = json.loads(Fernet(base64.urlsafe_b64encode(key)).decrypt(ciphertext.encode()))
        if value["context"] == context:
            return str(value["password"])
    except (InvalidToken, ValueError, KeyError, UnicodeError, AttributeError):
        pass
    return ""


def _context(user) -> str:
    if not user.tenant_id:
        raise ValueError("계정의 학원을 확인할 수 없습니다.")
    return f"user:{user.tenant_id}:{user.pk}"


def remember_account_password(user, password: str) -> None:
    if not password or not check_password(password, user.password):
        raise ValueError("로그인 비밀번호와 안내 비밀번호가 일치하지 않습니다.")
    user.account_notice_password_ciphertext = protect_password(password, context=_context(user))
    user.save(update_fields=["account_notice_password_ciphertext"])


@transaction.atomic
def account_notice_password(user, supplied: str | None = None, *, tenant_id: int | None = None) -> str:
    """Return a verified credential; never replace a password with prose.

Legacy hashes cannot be reversed. Issue an additional login credential, keeping
the existing password active until the recipient uses the delivered credential.
Repeated notices reuse the same credential rather than invalidating each other.
"""
    from django.contrib.auth import get_user_model
    from apps.core.models import PendingPasswordReset
    from .password import create_pending_password_reset, generate_temp_password

    if user is None or not user.is_active or (tenant_id is not None and user.tenant_id != tenant_id):
        raise ValueError("로그인할 수 있는 계정이 없습니다.")
    user = get_user_model().objects.select_for_update().get(pk=user.pk, tenant_id=user.tenant_id)
    context = _context(user)
    pending = PendingPasswordReset.objects.filter(user=user, tenant_id=user.tenant_id).first()
    from django.utils import timezone
    pending_valid = pending and pending.expires_at > timezone.now()
    stored = recover_password(user.account_notice_password_ciphertext, context=context)
    for candidate in (supplied, stored):
        if candidate and (
            check_password(candidate, user.password)
            or (pending_valid and check_password(candidate, pending.password_hash))
        ):
            if not stored and check_password(candidate, user.password):
                remember_account_password(user, candidate)
            return candidate
    # Account-notice credentials remain usable for 30 days; public password
    # recovery keeps its existing 30-minute TTL. Old API instances can consume both.
    password = generate_temp_password()
    create_pending_password_reset(user, password, ttl_minutes=60 * 24 * 30)
    user.account_notice_password_ciphertext = protect_password(password, context=context)
    user.save(update_fields=["account_notice_password_ciphertext"])
    return password
