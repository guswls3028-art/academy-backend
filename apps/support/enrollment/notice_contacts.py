"""Enrollment credential recipients shared by registration and the sending worker."""

from apps.shared.utils.student_contact import contact_phone, student_contact_phone


ENROLLMENT_NOTICE_PREFIX = "enrollment-notice:"


def registration_notice_contact(student, role):
    phone = student_contact_phone(student, role)
    if not phone:
        return "", None
    if role == "student":
        return phone, student.user_id
    parent = student.parent
    if not parent or parent.tenant_id != student.tenant_id or contact_phone(parent.phone) != phone:
        return "", None
    return phone, parent.user_id


def assert_enrollment_notice_delivery(data, *, business_tenant_id, recipient_id):
    """Bind this worker job to the role, account and destination approved on its receipt."""
    from apps.domains.enrollment.models import EnrollmentNoticeRecipient
    from apps.domains.messaging.security import build_recipient_fingerprint_candidates
    from apps.domains.students.models import Student

    raw_destination = data.get("to")
    destination = contact_phone(raw_destination) if isinstance(raw_destination, str) else ""
    business_key = data.get("business_idempotency_key")
    if (not destination or not business_key or data.get("target_type") != "account"
            or not recipient_id.isascii() or not recipient_id.isdecimal() or len(recipient_id) > 19
            or not 0 < int(recipient_id) <= 2**63 - 1):
        raise ValueError("recipient_contact_changed")
    recipient = EnrollmentNoticeRecipient.objects.filter(
        pk=int(recipient_id), registration__tenant_id=business_tenant_id, status="admitted",
        business_key=business_key, outbox_id__isnull=False,
    ).select_related("registration").only(
        "registration__lecture_id", "role", "student_id", "account_id", "recipient_fingerprint",
    ).first()
    if (not recipient or recipient.role not in ("student", "parent")
            or data.get("event_type") != f"registration_approved_{recipient.role}"
            or data.get("target_id") != f"{recipient.role}:{recipient.student_id}"):
        raise ValueError("recipient_contact_changed")

    students = Student.objects.filter(
        pk=recipient.student_id, tenant_id=business_tenant_id, deleted_at__isnull=True,
        enrollments__tenant_id=business_tenant_id, enrollments__lecture_id=recipient.registration.lecture_id,
        enrollments__status="ACTIVE", user__tenant_id=business_tenant_id, user__is_active=True,
    )
    if recipient.role == "parent":
        students = students.filter(parent__user__tenant_id=business_tenant_id, parent__user__is_active=True)
    student = students.select_related("parent").only(
        "phone", "parent_phone", "uses_identifier", "tenant_id", "user_id",
        "parent__tenant_id", "parent__phone", "parent__user_id",
    ).first()
    if not student:
        raise ValueError("recipient_contact_changed")
    phone, account_id = registration_notice_contact(student, recipient.role)
    fingerprints = build_recipient_fingerprint_candidates(destination)
    if (not account_id or account_id != recipient.account_id or phone != destination
            or recipient.recipient_fingerprint not in fingerprints or data.get("recipient_fingerprint") not in fingerprints):
        raise ValueError("recipient_contact_changed")
