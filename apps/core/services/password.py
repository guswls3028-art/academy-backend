# PATH: apps/core/services/password.py
"""
비밀번호 변경 SSOT.
모든 비밀번호 변경 경로(본인 변경, 관리자 리셋, 비밀번호 찾기 등)는
이 모듈의 함수를 통해야 한다. token_version 증가가 보장된다.
"""
from __future__ import annotations


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
