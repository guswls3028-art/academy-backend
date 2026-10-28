from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import Student
from apps.core.services.account_credentials import account_notice_password

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
    plaintext = str(value or "")
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


def lock_account_notice_users(student: Student) -> None:
    """Acquire account locks before Student, matching deletion/restoration order."""
    from django.contrib.auth import get_user_model

    user_ids = [student.user_id]
    if student.parent_id:
        user_ids.append(student.parent.user_id)
    list(get_user_model().objects.select_for_update().filter(
        pk__in=user_ids, tenant_id=student.tenant_id,
    ).order_by("pk"))


@transaction.atomic
def stage_pending_account_notice(
    *,
    student: Student,
    student_password: str,
    parent_password: str,
    origin_type: str = "",
    origin_id: str = "",
) -> None:
    """Store only encrypted one-time notice values until first enrollment."""
    lock_account_notice_users(student)
    Student.objects.select_for_update().only("id").get(pk=student.pk, tenant_id=student.tenant_id)
    parent_password = account_notice_password(student.parent.user, parent_password, tenant_id=student.tenant_id) if student.parent_id else student_password
    student_password = account_notice_password(student.user, student_password, tenant_id=student.tenant_id)
    student.pending_account_notice_student_password_ciphertext = _encrypt(
        student_password
    )
    student.pending_account_notice_parent_password_ciphertext = _encrypt(
        parent_password
    )
    student.pending_account_notice_since = timezone.now()
    student.pending_account_notice_origin_type = str(origin_type or "").strip()[:64]
    student.pending_account_notice_origin_id = str(origin_id or "").strip()[:128]
    student.save(
        update_fields=[
            "pending_account_notice_student_password_ciphertext",
            "pending_account_notice_parent_password_ciphertext",
            "pending_account_notice_since",
            "pending_account_notice_origin_type",
            "pending_account_notice_origin_id",
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


def initial_account_notice_state(student: Student) -> dict:
    """Explain automatic admission without claiming recipient delivery."""
    from apps.support.students.account_notice_dependencies import active_student_enrollment_exists

    pending = bool(
        student.pending_account_notice_student_password_ciphertext
        or student.pending_account_notice_parent_password_ciphertext
    )
    if not pending:
        return {
            "status": "not_pending",
            "message": "첫 수강 계정 안내 대기가 없습니다. 기존 학생의 추가 수강에는 자동으로 다시 보내지 않습니다. 필요하면 학생·학부모 로그인 정보 안내를 요청해 주세요.",
        }
    if not active_student_enrollment_exists(tenant_id=student.tenant_id, student_id=student.id):
        return {
            "status": "awaiting_enrollment",
            "message": "첫 수강 확정 전입니다. 수강이 확정되면 학생·학부모 계정 안내를 자동으로 요청합니다.",
        }
    return {
        "status": "action_required",
        "message": "수강은 확정됐지만 최초 계정 안내 접수가 완료되지 않았습니다. 최초 안내 다시 요청으로 접수되지 않은 안내를 복구할 수 있습니다.",
    }


def dispatch_pending_account_notice(*, student_id: int) -> dict:
    """Create the durable account-notice outbox after enrollment confirmation."""
    from apps.support.students.account_notice_dependencies import send_welcome_messages

    with transaction.atomic():
        snapshot = Student.objects.select_related("parent__user").filter(pk=student_id, deleted_at__isnull=True).first()
        if snapshot is None:
            return {"status": "skip", "reason": "student_missing"}
        lock_account_notice_users(snapshot)
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
        origin_type = (
            student.pending_account_notice_origin_type or "student_account"
        )
        origin_id = (
            student.pending_account_notice_origin_id or str(student.id)
        )
        if not student_ciphertext or not parent_ciphertext:
            return {"status": "skip", "reason": "no_pending_notice"}

        from apps.support.students.account_notice_dependencies import active_student_enrollment_exists
        if not active_student_enrollment_exists(tenant_id=student.tenant_id, student_id=student.id):
            return {"status": "skip", "reason": "enrollment_not_active"}

        try:
            student_password = _decrypt(student_ciphertext)
            parent_password = _decrypt(parent_ciphertext)
            parent_password = account_notice_password(student.parent.user, parent_password, tenant_id=student.tenant_id) if student.parent_id else student_password
            student_password = account_notice_password(student.user, student_password, tenant_id=student.tenant_id)
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
            origin_type=origin_type,
            origin_id=origin_id,
            first_enrollment_since=student.pending_account_notice_since,
        )
        if result.get("status") != "enqueued" or result.get("enqueued", 0) < expected:
            # Discard the new partial pair and its on_commit queue callbacks.
            # Existing durable rows remain and will be reused by an explicit retry.
            transaction.set_rollback(True)
            return {
                "status": "pending",
                "enqueued": 0,
                "expected": expected,
            }

        student.pending_account_notice_student_password_ciphertext = ""
        student.pending_account_notice_parent_password_ciphertext = ""
        student.pending_account_notice_since = None
        student.pending_account_notice_origin_type = ""
        student.pending_account_notice_origin_id = ""
        student.save(
            update_fields=[
                "pending_account_notice_student_password_ciphertext",
                "pending_account_notice_parent_password_ciphertext",
                "pending_account_notice_since",
                "pending_account_notice_origin_type",
                "pending_account_notice_origin_id",
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
