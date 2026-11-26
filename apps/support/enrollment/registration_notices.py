"""Register a lecture roster and its staff-selected account notices as one decision."""

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, ValidationError

from apps.domains.enrollment.models import Enrollment, EnrollmentNoticeRecipient, EnrollmentNoticeRegistration
from apps.domains.enrollment.selectors import get_lecture_for_tenant_or_404
from apps.domains.enrollment.services.lifecycle import bulk_create_enrollments
from apps.domains.messaging.models import NotificationLog, ScheduledNotification
from apps.domains.messaging.permissions import can_send_messages
from apps.domains.messaging.security import build_recipient_fingerprint, normalize_recipient_phone
from apps.domains.students.models import Student
from apps.domains.students.services.account_notice import PENDING_NOTICE_CLEARED


class RegistrationRequestConflict(APIException):
    status_code = 409
    default_detail = "이미 다른 등록 조건에 사용한 요청입니다. 기존 등록 결과를 확인해 주세요."


def can_select_notice_modes(request):
    return can_send_messages(request, request.tenant)


def _contact(student, role):
    if role == "student":
        phone = normalize_recipient_phone(student.phone)
        return (phone, student.user_id) if not student.uses_identifier else ("", None)
    phone = normalize_recipient_phone(student.parent_phone)
    parent = student.parent
    if not parent or parent.tenant_id != student.tenant_id or normalize_recipient_phone(parent.phone) != phone:
        return "", None
    return phone, parent.user_id


def _recipient(registration, student, role, first, now):
    mode = getattr(registration, role + "_notice")
    phone, account_id = _contact(student, role)
    status = "queued"
    if mode == "skip":
        status = "skipped"
    elif mode == "auto" and not first:
        status = "not_first"
    elif not phone or len(phone) < 10 or not account_id:
        status = "no_recipient"
    elif (role == "student" and mode == "auto" and registration.parent_notice != "skip"
          and phone == _contact(student, "parent")[0]):
        status = "shared_recipient"
    initial = mode == "auto" and first and status == "queued"
    return EnrollmentNoticeRecipient(
        registration=registration, student_id=student.pk, role=role, status=status,
        recipient_fingerprint=build_recipient_fingerprint(phone) if status == "queued" else "",
        account_id=account_id if status == "queued" else None,
        initial_notice_since=student.pending_account_notice_since if initial else None,
        source_origin_type=student.pending_account_notice_origin_type if initial else "",
        source_origin_id=student.pending_account_notice_origin_id if initial else "",
        next_attempt_at=now if status == "queued" else None,
    )


@transaction.atomic
def register_with_notices(*, tenant, actor_id, lecture, students, request_id, student_notice, parent_notice):
    lecture_obj = get_lecture_for_tenant_or_404(tenant=tenant, lecture_id=lecture)
    ids = sorted(set(students))
    registration, new = EnrollmentNoticeRegistration.objects.get_or_create(
        tenant=tenant, actor_id=actor_id, request_id=request_id,
        defaults=dict(lecture=lecture_obj, student_ids=ids, student_notice=student_notice, parent_notice=parent_notice),
    )
    if not new:
        if (registration.lecture_id, registration.student_ids, registration.student_notice, registration.parent_notice) != (
            lecture, ids, student_notice, parent_notice,
        ):
            raise RegistrationRequestConflict()
        return registration

    # Serialize decisions across lectures and legacy write paths, before observing first enrollment.
    roster = list(Student.objects.select_for_update(of=("self",)).select_related("parent")
                  .filter(tenant=tenant, pk__in=ids, deleted_at__isnull=True).order_by("pk"))
    if len(roster) != len(ids):
        raise ValidationError({"students": "현재 학원의 활성 학생만 선택할 수 있습니다."})
    already_active = set(Enrollment.objects.filter(
        tenant=tenant, student_id__in=ids, status="ACTIVE",
    ).values_list("student_id", flat=True))
    bulk_create_enrollments(tenant=tenant, lecture_id=lecture, student_ids=ids, schedule_account_notice=False)
    recipients = []
    consumed = []
    now = timezone.now()
    for student in roster:
        first = student.pk not in already_active and bool(
            student.pending_account_notice_since or student.pending_account_notice_student_password_ciphertext
            or student.pending_account_notice_parent_password_ciphertext
        )
        if first:
            consumed.append(student.pk)
        for role in ("student", "parent"):
            recipients.append(_recipient(registration, student, role, first, now))
    EnrollmentNoticeRecipient.objects.bulk_create(recipients)
    if consumed:
        # A later lecture, retry of the old API, or initial-notice recovery must respect this decision.
        Student.objects.filter(tenant=tenant, pk__in=consumed).update(**PENDING_NOTICE_CLEARED, updated_at=now)
    return registration


def find_registration(*, tenant, actor_id, lecture, request_id=None):
    get_lecture_for_tenant_or_404(tenant=tenant, lecture_id=lecture)
    rows = EnrollmentNoticeRegistration.objects.filter(tenant=tenant, actor_id=actor_id, lecture_id=lecture)
    if request_id:
        rows = rows.filter(request_id=request_id)
    return rows.order_by("-pk").first()


def _delivery(recipient, outboxes, logs):
    status = recipient.status
    log = logs.get(recipient.business_key)
    if status in ("delivered", "provider_failed"):
        status = "delivered" if status == "delivered" else "failed"
    elif log:
        if log.status == "sent":
            status = "accepted" if log.success and log.provider_message_id else "ambiguous"
        elif log.status in ("failed", "ambiguous"):
            status = log.status
        elif log.status in ("processing", "sending", "retryable_failed"):
            status = "processing"
        else:
            status = "ambiguous"
    elif status == "admitted":
        state = outboxes.get(recipient.outbox_id)
        if state in ("failed", "cancelled"):
            status = "failed"
        elif state == "sent":
            status = "processing"
        elif state in ("pending", "dispatching"):
            status = "queued"
        else:
            status = "ambiguous"
    return {"status": status, "log_id": log.pk if log else None}


def verify_registration_notice(*, tenant, actor_id, lecture, request_id, log_id):
    from apps.domains.messaging.provider_delivery import get_provider_delivery_status

    registration = find_registration(tenant=tenant, actor_id=actor_id, lecture=lecture, request_id=request_id)
    if not registration:
        raise NotFound("등록 결과를 찾을 수 없습니다.")
    log = NotificationLog.objects.filter(
        Q(source_tenant=tenant) | Q(source_tenant__isnull=True, tenant=tenant),
        pk=log_id, message_mode="alimtalk",
    ).exclude(business_idempotency_key="").first()
    if not log or not log.provider_message_id:
        raise NotFound("공급사 접수 이력을 찾을 수 없습니다.")
    recipient = registration.recipients.filter(business_key=log.business_idempotency_key).first()
    if not recipient:
        raise NotFound("이 수강 등록에 해당하는 발송 이력이 없습니다.")
    provider = get_provider_delivery_status(log)
    terminal = {"delivered": "delivered", "failed": "provider_failed"}.get(provider["status"])
    if terminal:
        registration.recipients.filter(pk=recipient.pk, status="admitted").update(status=terminal)
    return {
        "provider_delivery_status": provider["status"], "provider_status_code": provider["status_code"],
        "provider_delivery_checked_at": provider["checked_at"], "provider_delivery_updated_at": provider["updated_at"],
        "provider_delivery_failure_reason": provider["failure_reason"],
    }


def registration_result(registration):
    recipients = list(registration.recipients.all())
    tenant_id = registration.tenant_id
    names = dict(Student.objects.filter(tenant_id=tenant_id, pk__in=registration.student_ids)
                 .values_list("pk", "name"))
    outboxes = dict(ScheduledNotification.objects.filter(
        tenant_id=tenant_id, pk__in=[r.outbox_id for r in recipients if r.outbox_id],
    ).values_list("pk", "status"))
    keys = [r.business_key for r in recipients if r.business_key]
    logs = {row.business_idempotency_key: row for row in NotificationLog.objects.filter(
        Q(source_tenant_id=tenant_id) | Q(source_tenant__isnull=True, tenant_id=tenant_id),
        message_mode="alimtalk", business_idempotency_key__in=keys,
    ).only("id", "business_idempotency_key", "status", "success", "provider_message_id")}
    students = {sid: {"id": sid, "name": names.get(sid, "삭제된 학생")} for sid in registration.student_ids}
    for recipient in recipients:
        students[recipient.student_id][recipient.role] = _delivery(recipient, outboxes, logs)
    return {"request_id": str(registration.request_id), "lecture": registration.lecture_id,
            "students": list(students.values())}
