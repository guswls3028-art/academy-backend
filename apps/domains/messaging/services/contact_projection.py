"""Current staff-visible contacts, separate from immutable delivery evidence."""

import re

from apps.support.messaging.student_dependencies import students_for_tenant


def current_recipient_phones(tenant, logs):
    """Resolve exact student references in one tenant-scoped query per page.

    Stored summaries and diagnostic logs stay redacted. Unknown references,
    deleted students and identifier-only student accounts have no phone here.
    A current contact is not proof of the historical delivery destination.
    """
    targets = {}
    for log in logs:
        if (log.source_tenant_id or log.tenant_id) != tenant.pk:
            continue
        target_id = str(log.target_id or "")
        if log.target_type in {"student", "parent"} and re.fullmatch(r"[1-9][0-9]{0,17}", target_id):
            targets[log.pk] = (int(target_id), log.target_type)
        # Account keys can contain a legacy user-chosen ps_number, including
        # numeric parent:<value> fallbacks. They are not provable student PKs.
    if not targets:
        return {}
    students = {
        row["id"]: row
        for row in students_for_tenant(tenant, deleted="active").filter(
            id__in={student_id for student_id, _ in targets.values()},
        ).values("id", "phone", "parent_phone", "uses_identifier")
    }
    contacts = {}
    for log_id, (student_id, role) in targets.items():
        student = students.get(student_id)
        if student is None:
            continue
        if role == "parent":
            contacts[log_id] = student["parent_phone"] or ""
        else:
            contacts[log_id] = "" if student["uses_identifier"] else student["phone"] or ""
    return contacts
