"""Student selector dependencies for messaging services."""

from __future__ import annotations

from apps.shared.utils.student_contact import contact_phone, student_contact_phone
from apps.support.enrollment.notice_contacts import ENROLLMENT_NOTICE_PREFIX, assert_enrollment_notice_delivery


def students_for_tenant(tenant, *, deleted: str = "active"):
    from apps.domains.students.selectors import students_for_tenant as _students_for_tenant

    return _students_for_tenant(tenant, deleted=deleted)


def assert_current_delivery_contact(data, *, business_tenant_id: int) -> None:
    """Check the signed roster destination without retargeting a queued delivery."""
    reference = str(data.get("domain_object_id") or "")
    if reference.startswith(ENROLLMENT_NOTICE_PREFIX):
        assert_enrollment_notice_delivery(
            data, business_tenant_id=business_tenant_id, recipient_id=reference[len(ENROLLMENT_NOTICE_PREFIX):],
        )
        return

    role = data.get("target_type")
    if role not in ("student", "parent"):
        return
    student_id = str(data.get("target_id") or "")
    if not (student_id.isascii() and student_id.isdecimal() and 0 < len(student_id) <= 18):
        raise ValueError("recipient_contact_changed")

    deleted = "deleted" if data.get("event_type") == "withdrawal_complete" else "active"
    student = students_for_tenant(business_tenant_id, deleted=deleted).filter(
        pk=int(student_id),
    ).only("phone", "parent_phone", "uses_identifier").first()
    destination = contact_phone(data.get("to"))
    if not destination or student is None or student_contact_phone(student, role) != destination:
        raise ValueError("recipient_contact_changed")

