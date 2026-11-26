"""Prepare one credential notice from its durable choice on the scheduled API role."""

import logging
import time
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from apps.core.services.account_credentials import account_notice_password
from apps.domains.enrollment.models import Enrollment, EnrollmentNoticeRecipient
from apps.domains.messaging.security import build_recipient_fingerprint_candidates
from apps.domains.messaging.services.registration_service import send_welcome_messages
from apps.domains.students.models import Student
from .registration_notices import _contact

logger = logging.getLogger(__name__)


class NoticeAdmissionUnavailable(Exception):
    """No durable provider-bound outbox was created."""


def _admit(recipient):
    batch = recipient.registration
    students = Student.objects.filter(pk=recipient.student_id, tenant_id=batch.tenant_id, deleted_at__isnull=True)
    snapshot = students.values("user_id", "parent__user_id").first()
    if not snapshot:
        return "failed"
    # Same User -> Student lock order as account edits/deletion, then recheck the binding.
    accounts = get_user_model().objects.select_for_update().filter(
        tenant_id=batch.tenant_id, pk__in=[pk for pk in snapshot.values() if pk],
    ).order_by("pk")
    locked = {user.pk: user for user in accounts}
    student = students.select_for_update(of=("self",)).select_related("tenant", "parent").first()
    if not student or student.user_id not in locked:
        return "failed"
    if not Enrollment.objects.filter(tenant_id=batch.tenant_id, lecture_id=batch.lecture_id,
                                     student_id=student.pk, status="ACTIVE").exists():
        return "failed"
    phone, user_id = _contact(student, recipient.role)
    if (user_id != recipient.account_id or user_id not in locked or not locked[user_id].is_active
            or recipient.recipient_fingerprint not in build_recipient_fingerprint_candidates(phone)):
        return "failed"
    student_password = account_notice_password(locked[student.user_id], tenant_id=batch.tenant_id)
    parent_passwords = {}
    if recipient.role == "parent":
        parent_passwords[student.parent_phone] = account_notice_password(locked[user_id], tenant_id=batch.tenant_id)
    outboxes = []
    send_welcome_messages(
        created_students=[student], student_password=student_password, parent_password_by_phone=parent_passwords,
        recipient_roles=(recipient.role,), admitted_outboxes=outboxes, first_enrollment_since=recipient.initial_notice_since,
        origin_type=recipient.source_origin_type or "enrollment_registration",
        origin_id=recipient.source_origin_id or f"registration:{batch.pk}:{recipient.pk}",
    )
    if len(outboxes) != 1:
        raise NoticeAdmissionUnavailable()
    outbox = outboxes[0]
    recipient.outbox_id = outbox.pk
    recipient.business_key = outbox.business_idempotency_key
    return "admitted"


@transaction.atomic
def _process_recipient(recipient_id, tenant_id):
    now = timezone.now()
    recipient = (EnrollmentNoticeRecipient.objects.select_for_update(of=("self",), skip_locked=True)
                 .select_related("registration")
                 .filter(pk=recipient_id, registration__tenant_id=tenant_id, status="queued",
                         next_attempt_at__lte=now).first())
    if not recipient:
        return False
    recipient.attempts += 1
    exhausted = False
    try:
        # Roll back credentials, outbox and on_commit hooks together on pre-admission failure.
        with transaction.atomic():
            recipient.status = _admit(recipient)
    except Exception:
        logger.exception("enrollment notice admission failed tenant=%s recipient=%s attempt=%s",
                         tenant_id, recipient.pk, recipient.attempts)
        if recipient.attempts >= 3:
            recipient.status = "failed"
            exhausted = True
    recipient.next_attempt_at = now + timedelta(minutes=recipient.attempts) if recipient.status == "queued" else None
    recipient.save(update_fields=["status", "next_attempt_at", "attempts", "outbox_id", "business_key"])
    return exhausted


def process_registration_notices(*, limit, time_budget=20):
    due = EnrollmentNoticeRecipient.objects.filter(status="queued", next_attempt_at__lte=timezone.now())
    jobs = list(due.order_by("next_attempt_at", "pk").values_list("pk", "registration__tenant_id")[:limit])
    failed = 0
    deadline = time.monotonic() + time_budget
    for recipient_id, tenant_id in jobs:
        if time.monotonic() >= deadline:
            break
        failed += int(_process_recipient(recipient_id, tenant_id))
    if failed:
        logger.error("enrollment notice terminal admission failures count=%s", failed)
    return failed
