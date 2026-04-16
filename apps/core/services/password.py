# PATH: apps/core/services/password.py
"""
비밀번호 변경 SSOT.
모든 비밀번호 변경 경로(본인 변경, 관리자 리셋, 비밀번호 찾기 등)는
이 모듈의 함수를 통해야 한다. token_version 증가가 보장된다.
"""
from __future__ import annotations

from django.db import models


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
    관리자에 의한 강제 비밀번호 리셋.
    must_change_password는 건드리지 않는다 (호출자가 필요 시 별도 설정).
    """
    user.set_password(new_password)
    user.token_version = (getattr(user, "token_version", 0) or 0) + 1
    user.save(update_fields=["password", "token_version"])


def rollback_password(user, old_password_hash: str) -> None:
    """
    비밀번호 변경 후 알림톡 발송 실패 등으로 롤백할 때 사용.
    token_version은 롤백하지 않는다 (이미 변경된 토큰은 무효화 유지).
    """
    user.password = old_password_hash
    user.save(update_fields=["password"])
