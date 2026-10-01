"""Initial-password policy for student Excel imports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

StudentImportPasswordMode = Literal["fixed", "phone_last4", "random"]

FIXED_PASSWORD_MODE: StudentImportPasswordMode = "fixed"
RANDOM_PASSWORD_MODE: StudentImportPasswordMode = "random"
VALID_PASSWORD_MODES = frozenset(
    {
        FIXED_PASSWORD_MODE,
        RANDOM_PASSWORD_MODE,
        "phone_last4",
    }
)


class StudentImportPasswordError(ValueError):
    """Raised when an Excel import password policy cannot be applied safely."""


@dataclass(frozen=True)
class StudentImportPasswordPolicy:
    mode: StudentImportPasswordMode
    fixed_password: str = ""
    tenant: Any = None

    def password_for_row(self, row: dict[str, Any]) -> str:
        from apps.core.services.initial_password_policy import initial_password
        from .identity import canonical_student_phone

        try:
            return initial_password(
                self.tenant,
                role="student",
                phone=canonical_student_phone(
                    phone=row.get("phone") or row.get("studentPhone"),
                    parent_phone=row.get("parent_phone") or row.get("parentPhone"),
                ),
                supplied=self.fixed_password or None,
                mode=self.mode,
            )
        except ValueError as exc:
            raise StudentImportPasswordError(str(exc)) from exc


def build_student_import_password_policy(
    *,
    password_mode: str | None,
    initial_password: str | None,
    tenant: Any = None,
) -> StudentImportPasswordPolicy:
    from apps.core.services.initial_password_policy import password_settings

    if initial_password is not None and not isinstance(initial_password, str):
        raise StudentImportPasswordError("초기 비밀번호는 문자열이어야 합니다.")
    fixed_password = initial_password or ""
    normalized_mode = password_mode
    if normalized_mode is None and fixed_password:
        normalized_mode = FIXED_PASSWORD_MODE
    elif normalized_mode is None or normalized_mode == "tenant":
        if tenant is None:
            raise StudentImportPasswordError("학생 초기 비밀번호 방식을 선택해 주세요.")
        policy = password_settings(tenant)
        normalized_mode = policy["student_mode"]
        if not fixed_password and normalized_mode == FIXED_PASSWORD_MODE:
            fixed_password = policy["student_fixed_password"]
    if normalized_mode not in VALID_PASSWORD_MODES:
        raise StudentImportPasswordError(
            "학생 초기 비밀번호 방식을 선택해 주세요. fixed, phone_last4 또는 random이어야 합니다."
        )

    if normalized_mode == FIXED_PASSWORD_MODE and len(fixed_password) < 4:
        raise StudentImportPasswordError("공통 초기 비밀번호는 4자 이상이어야 합니다.")
    if normalized_mode != FIXED_PASSWORD_MODE and fixed_password:
        raise StudentImportPasswordError("선택한 방식과 직접 입력 비밀번호가 일치하지 않습니다.")

    return StudentImportPasswordPolicy(
        mode=normalized_mode,
        tenant=tenant,
        fixed_password=fixed_password if normalized_mode == FIXED_PASSWORD_MODE else "",
    )
