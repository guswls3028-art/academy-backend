"""Initial-password policy for student Excel imports."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Literal

StudentImportPasswordMode = Literal["fixed", "random", "tenant"]

FIXED_PASSWORD_MODE: StudentImportPasswordMode = "fixed"
RANDOM_PASSWORD_MODE: StudentImportPasswordMode = "random"
VALID_PASSWORD_MODES = frozenset(
    {
        FIXED_PASSWORD_MODE,
        RANDOM_PASSWORD_MODE,
        "tenant",
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
        if self.mode == "tenant":
            if self.tenant is None:
                raise StudentImportPasswordError("학원 초기 비밀번호 설정을 확인할 수 없습니다.")
            from apps.core.services.initial_password_policy import initial_password
            from .identity import canonical_student_phone
            return initial_password(self.tenant, role="student", phone=canonical_student_phone(phone=row.get("phone"), parent_phone=row.get("parent_phone")))
        if self.mode == FIXED_PASSWORD_MODE:
            return self.fixed_password
        return f"{secrets.randbelow(1_000_000):06d}"


def build_student_import_password_policy(
    *,
    password_mode: str | None,
    initial_password: str | None,
    tenant: Any = None,
) -> StudentImportPasswordPolicy:
    normalized_mode = str(password_mode or FIXED_PASSWORD_MODE).strip().lower()
    if normalized_mode not in VALID_PASSWORD_MODES:
        raise StudentImportPasswordError(
            "password_mode는 fixed, random 또는 tenant이어야 합니다."
        )

    fixed_password = str(initial_password or "").strip()
    if normalized_mode == FIXED_PASSWORD_MODE and len(fixed_password) < 4:
        raise StudentImportPasswordError("공통 초기 비밀번호는 4자 이상이어야 합니다.")

    return StudentImportPasswordPolicy(
        mode=normalized_mode,
        tenant=tenant,
        fixed_password=fixed_password if normalized_mode == FIXED_PASSWORD_MODE else "",
    )
