from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import Student

logger = logging.getLogger(__name__)

ACCOUNT_NOTICE_SECRET_PREFIX = "student-account-notice:v1:"


class AccountNoticeSecretError(ValueError):
    """A pending account notice secret cannot be protected or recovered."""


def _fernet() -> Fernet:
    secret_key = str(getattr(settings, "SECRET_KEY", "") or "")
    if not secret_key:
        raise AccountNoticeSecretError("account_notice_secret_key_missing")
    digest = hashlib.sha256(
        f"academy:student-account-notice:v1:{secret_key}".encode("utf-8")
    ).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _encrypt(value: str) -> str:
    plaintext = str(value or "").strip()
    if not plaintext:
        raise AccountNoticeSecretError("account_notice_secret_empty")
    token = _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")
    return f"{ACCOUNT_NOTICE_SECRET_PREFIX}{token}"


def _decrypt(value: str) -> str:
    stored = str(value or "")
    if not stored.startswith(ACCOUNT_NOTICE_SECRET_PREFIX):
        raise AccountNoticeSecretError("account_notice_secret_format_invalid")
    token = stored[len(ACCOUNT_NOTICE_SECRET_PREFIX):].encode("ascii")
    try:
        return _fernet().decrypt(token).decode("utf-8")
    except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
        raise AccountNoticeSecretError(
            "account_notice_secret_decryption_failed"
        ) from exc


def stage_pending_account_notice(
    *,
    student: Student,
    student_password: str,
    parent_password: str,
) -> None:
    """Store only encrypted one-time notice values until first enrollment."""
    student.pending_account_notice_student_password_ciphertext = _encrypt(
        student_password
    )
    student.pending_account_notice_parent_password_ciphertext = _encrypt(
        parent_password
    )
    student.pending_account_notice_since = timezone.now()
    student.save(
        update_fields=[
            "pending_account_notice_student_password_ciphertext",
            "pending_account_notice_parent_password_ciphertext",
            "pending_account_notice_since",
            "updated_at",
        ]
    )


def _expected_recipient_count(student: Student) -> int:
    phone = (student.phone or "").replace("-", "").strip()
    parent_phone = (student.parent_phone or "").replace("-", "").strip()
    parent_count = int(len(parent_phone) >= 10)
    student_count = int(
        len(phone) >= 10 and phone != parent_phone
    )
    return parent_count + student_count


def dispatch_pending_account_notice(*, student_id: int) -> dict:
    """Create the durable account-notice outbox after enrollment confirmation."""
    from apps.support.students.account_notice_dependencies import send_welcome_messages

    with transaction.atomic():
        student = (
            Student.objects.select_for_update()
            .select_related("tenant")
            .filter(pk=student_id, deleted_at__isnull=True)
            .first()
        )
        if student is None:
            return {"status": "skip", "reason": "student_missing"}

        student_ciphertext = student.pending_account_notice_student_password_ciphertext
        parent_ciphertext = student.pending_account_notice_parent_password_ciphertext
        if not student_ciphertext or not parent_ciphertext:
            return {"status": "skip", "reason": "no_pending_notice"}

        try:
            student_password = _decrypt(student_ciphertext)
            parent_password = _decrypt(parent_ciphertext)
        except AccountNoticeSecretError:
            logger.exception(
                "pending account notice secret invalid: student_id=%s",
                student_id,
            )
            return {"status": "error", "reason": "secret_invalid"}

        expected = _expected_recipient_count(student)
        if expected == 0:
            logger.error(
                "pending account notice has no valid recipient: student_id=%s",
                student_id,
            )
            return {"status": "error", "reason": "recipient_missing"}

        result = send_welcome_messages(
            created_students=[student],
            student_password=student_password,
            parent_password_by_phone={student.parent_phone: parent_password},
        )
        if result.get("status") != "enqueued" or result.get("enqueued", 0) < expected:
            return {
                "status": "pending",
                "enqueued": result.get("enqueued", 0),
                "expected": expected,
            }

        student.pending_account_notice_student_password_ciphertext = ""
        student.pending_account_notice_parent_password_ciphertext = ""
        student.pending_account_notice_since = None
        student.save(
            update_fields=[
                "pending_account_notice_student_password_ciphertext",
                "pending_account_notice_parent_password_ciphertext",
                "pending_account_notice_since",
                "updated_at",
            ]
        )
        return {"status": "enqueued", "enqueued": result["enqueued"]}


def schedule_pending_account_notice(*, student_id: int) -> None:
    """Run only after the enrollment transaction commits."""
    def dispatch_safely() -> None:
        try:
            dispatch_pending_account_notice(student_id=student_id)
        except Exception:
            logger.exception(
                "pending account notice dispatch failed after enrollment: student_id=%s",
                student_id,
            )

    transaction.on_commit(dispatch_safely)
