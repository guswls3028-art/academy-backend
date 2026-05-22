# PATH: apps/domains/students/services/account_recovery.py
"""
Public account recovery for student/parent login accounts.

Canonical flow:
- caller proves knowledge of tenant + student name + registered phone
- API response never returns the username/password directly
- delivery happens only to the verified phone number
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q

from apps.core.models.user import user_display_username
from apps.core.services.password import generate_temp_password
from apps.domains.students.models import Student


RECOVERY_MODES = ("username", "password")
RECOVERY_TARGETS = ("student", "parent")


class AccountRecoveryError(Exception):
    """Base recovery error with a user-facing detail."""

    def __init__(self, detail: str):
        self.detail = detail
        super().__init__(detail)


class AccountRecoveryDeliveryError(AccountRecoveryError):
    """Raised when a matched account could not be notified."""


class AccountRecoveryValidationError(AccountRecoveryError):
    """Raised for invalid request payloads."""


@dataclass(frozen=True)
class RecoveryAccount:
    target: str
    student: Student
    user: object
    send_to: str
    display_name: str
    display_username: str


def normalize_recovery_phone(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits if len(digits) == 11 and digits.startswith("010") else ""


def validate_recovery_payload(*, mode: str, target: str, name: str, phone: str) -> tuple[str, str, str, str]:
    mode = str(mode or "").strip().lower()
    target = str(target or "").strip().lower()
    name = str(name or "").strip()
    phone = normalize_recovery_phone(phone)

    if mode not in RECOVERY_MODES:
        raise AccountRecoveryValidationError("복구 유형을 선택해 주세요.")
    if target not in RECOVERY_TARGETS:
        raise AccountRecoveryValidationError("대상을 선택해 주세요. (학생 / 학부모)")
    if not name:
        raise AccountRecoveryValidationError("학생 이름을 입력해 주세요.")
    if not phone:
        raise AccountRecoveryValidationError("휴대폰 번호를 010 11자리로 입력해 주세요.")
    return mode, target, name, phone


def _find_unique_student(*, tenant, name: str, phone: str, parent_only: bool) -> Student | None:
    qs = Student.objects.filter(
        tenant=tenant,
        deleted_at__isnull=True,
        name__iexact=name,
    )
    if parent_only:
        qs = qs.filter(parent_phone=phone)
    else:
        qs = qs.filter(Q(phone=phone) | Q(parent_phone=phone))

    matches = list(qs.select_related("user").order_by("id")[:2])
    if len(matches) != 1:
        return None
    return matches[0]


def resolve_recovery_account(*, tenant, target: str, name: str, phone: str) -> RecoveryAccount | None:
    """Return a uniquely matched account, or None for no/ambiguous matches."""

    if target == "student":
        student = _find_unique_student(tenant=tenant, name=name, phone=phone, parent_only=False)
        if not student or not getattr(student, "user_id", None):
            return None
        return RecoveryAccount(
            target="student",
            student=student,
            user=student.user,
            send_to=phone,
            display_name=student.name or name,
            display_username=student.ps_number or user_display_username(student.user),
        )

    student = _find_unique_student(tenant=tenant, name=name, phone=phone, parent_only=True)
    if not student:
        return None

    # Existing production behavior: if a legacy student has no Parent account yet,
    # create/link it after the caller proves student name + parent phone.
    from apps.domains.parents.models import Parent
    from apps.domains.parents.services import ensure_parent_for_student

    ensure_parent_for_student(
        tenant=tenant,
        parent_phone=phone,
        student_name=student.name,
    )
    parent = Parent.objects.filter(tenant=tenant, phone=phone).select_related("user").first()
    if not parent or not getattr(parent, "user_id", None):
        return None

    return RecoveryAccount(
        target="parent",
        student=student,
        user=parent.user,
        send_to=phone,
        display_name=parent.name or f"{student.name} 학부모",
        display_username=phone,
    )


def _site_url() -> str:
    return getattr(settings, "SITE_URL", "") or "https://hakwonplus.com"


def _account_recovery_delivery_disabled(source_tenant_id: int) -> bool:
    from apps.domains.messaging.policy import is_messaging_disabled

    owner_tenant_id = getattr(settings, "OWNER_TENANT_ID", None)
    return is_messaging_disabled(source_tenant_id) or (
        bool(owner_tenant_id) and is_messaging_disabled(owner_tenant_id)
    )


def _send_owner_alimtalk(
    *,
    source_tenant_id: int,
    trigger: str,
    to: str,
    replacements: dict[str, str],
) -> bool:
    from apps.domains.messaging.policy import send_alimtalk_via_owner

    if _account_recovery_delivery_disabled(source_tenant_id):
        return True
    return send_alimtalk_via_owner(trigger=trigger, to=to, replacements=replacements)


def send_username_recovery(account: RecoveryAccount) -> None:
    """Send username only. Password is not changed."""

    notice = "비밀번호를 잊으셨다면 비밀번호 찾기에서 임시 비밀번호를 받아 주세요."
    if account.target == "parent":
        ok = _send_owner_alimtalk(
            source_tenant_id=account.student.tenant_id,
            trigger="registration_approved_parent",
            to=account.send_to,
            replacements={
                "학생이름": account.student.name or "",
                "학생아이디": account.student.ps_number or "",
                "학생비밀번호": "변경되지 않음",
                "학부모아이디": account.display_username,
                "학부모비밀번호": "변경되지 않음",
                "사이트링크": _site_url(),
                "비밀번호안내": notice,
            },
        )
    else:
        ok = _send_owner_alimtalk(
            source_tenant_id=account.student.tenant_id,
            trigger="registration_approved_student",
            to=account.send_to,
            replacements={
                "학생이름": account.display_name,
                "학생아이디": account.display_username,
                "학생비밀번호": "변경되지 않음",
                "사이트링크": _site_url(),
                "비밀번호안내": notice,
            },
        )

    if not ok:
        raise AccountRecoveryDeliveryError("아이디 안내 발송에 실패했습니다. 잠시 후 다시 시도해 주세요.")


def send_password_recovery(account: RecoveryAccount, *, temp_password: str | None = None) -> None:
    """Create a pending temporary password and notify the verified phone."""

    password = (temp_password or "").strip() or generate_temp_password()
    if len(password) < 4:
        raise AccountRecoveryValidationError("임시 비밀번호는 최소 4자 이상이어야 합니다.")

    user = account.user
    if _account_recovery_delivery_disabled(account.student.tenant_id):
        return

    from apps.core.services.password import (
        create_pending_password_reset,
        restore_pending_password_reset,
        snapshot_pending_password_reset,
    )

    previous_pending = snapshot_pending_password_reset(user)
    create_pending_password_reset(user, password)

    notice = "로그인 후 설정에서 비밀번호를 변경하실 수 있습니다."
    trigger = "password_reset_parent" if account.target == "parent" else "password_reset_student"
    replacements = {
        "학생이름": account.display_name or "",
        "학생아이디": account.display_username or "",
        "학생비밀번호": password,
        "아이디": account.display_username or "",
        "임시비밀번호": password,
        "비밀번호안내": notice,
        "사이트링크": _site_url(),
    }
    if account.target == "parent":
        replacements["학생이름"] = account.student.name or ""
        replacements["학부모아이디"] = account.display_username or ""
        replacements["학부모비밀번호"] = password

    if _send_owner_alimtalk(
        source_tenant_id=account.student.tenant_id,
        trigger=trigger,
        to=account.send_to,
        replacements=replacements,
    ):
        return

    restore_pending_password_reset(user, previous_pending)
    raise AccountRecoveryDeliveryError("임시 비밀번호 발송에 실패했습니다. 잠시 후 다시 시도해 주세요.")
