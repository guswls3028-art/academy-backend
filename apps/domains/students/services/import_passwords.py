"""Initial-password policy for student Excel imports."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any, Literal

StudentImportPasswordMode = Literal["fixed", "phone_last4", "random"]

FIXED_PASSWORD_MODE: StudentImportPasswordMode = "fixed"
PHONE_LAST4_PASSWORD_MODE: StudentImportPasswordMode = "phone_last4"
RANDOM_PASSWORD_MODE: StudentImportPasswordMode = "random"
VALID_PASSWORD_MODES = frozenset(
    {
        FIXED_PASSWORD_MODE,
        PHONE_LAST4_PASSWORD_MODE,
        RANDOM_PASSWORD_MODE,
    }
)


class StudentImportPasswordError(ValueError):
    """Raised when an Excel import password policy cannot be applied safely."""


def _digits(value: Any) -> str:
    return "".join(char for char in str(value or "") if char.isdigit())


def _student_phone(row: dict[str, Any]) -> str:
    return _digits(row.get("phone") or row.get("studentPhone"))


@dataclass(frozen=True)
class StudentImportPasswordPolicy:
    mode: StudentImportPasswordMode
    fixed_password: str = ""

    def validate_rows(self, students_data: list[dict[str, Any]]) -> None:
        if self.mode != PHONE_LAST4_PASSWORD_MODE:
            return

        invalid_rows: list[str] = []
        for row_index, raw in enumerate(students_data, start=1):
            row = raw if isinstance(raw, dict) else {}
            phone = _student_phone(row)
            uses_identifier = bool(
                row.get("uses_identifier")
                if "uses_identifier" in row
                else row.get("usesIdentifier")
            )
            if len(phone) == 11 and phone.startswith("010") and not uses_identifier:
                continue
            name = str(row.get("name") or "").strip() or f"{row_index}행"
            invalid_rows.append(name)

        if invalid_rows:
            preview = ", ".join(invalid_rows[:5])
            suffix = f" 외 {len(invalid_rows) - 5}명" if len(invalid_rows) > 5 else ""
            raise StudentImportPasswordError(
                "휴대폰 번호 뒤 4자리를 사용하려면 모든 학생의 학생 전화번호가 "
                f"010으로 시작하는 11자리여야 합니다. 확인할 학생: {preview}{suffix}"
            )

    def password_for_row(self, row: dict[str, Any]) -> str:
        if self.mode == FIXED_PASSWORD_MODE:
            return self.fixed_password
        if self.mode == PHONE_LAST4_PASSWORD_MODE:
            phone = _student_phone(row)
            uses_identifier = bool(
                row.get("uses_identifier")
                if "uses_identifier" in row
                else row.get("usesIdentifier")
            )
            if len(phone) != 11 or not phone.startswith("010") or uses_identifier:
                raise StudentImportPasswordError(
                    "학생 전화번호가 없어 휴대폰 번호 뒤 4자리를 초기 비밀번호로 사용할 수 없습니다."
                )
            return phone[-4:]
        return f"{secrets.randbelow(10_000):04d}"


def build_student_import_password_policy(
    *,
    password_mode: str | None,
    initial_password: str | None,
) -> StudentImportPasswordPolicy:
    normalized_mode = str(password_mode or FIXED_PASSWORD_MODE).strip().lower()
    if normalized_mode not in VALID_PASSWORD_MODES:
        raise StudentImportPasswordError(
            "password_mode는 fixed, phone_last4, random 중 하나여야 합니다."
        )

    fixed_password = str(initial_password or "").strip()
    if normalized_mode == FIXED_PASSWORD_MODE and len(fixed_password) < 4:
        raise StudentImportPasswordError("공통 초기 비밀번호는 4자 이상이어야 합니다.")

    return StudentImportPasswordPolicy(
        mode=normalized_mode,
        fixed_password=fixed_password if normalized_mode == FIXED_PASSWORD_MODE else "",
    )
