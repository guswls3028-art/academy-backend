# PATH: apps/domains/messaging/services/recipients.py
"""
Canonical recipient resolution for messaging entrypoints.

External delivery still belongs to the queue/worker layer. This module only
turns tenant-scoped student IDs into ordered recipient candidates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from apps.support.messaging.student_dependencies import students_for_tenant

RecipientTarget = Literal["student", "parent"]


@dataclass(frozen=True)
class StudentMessageRecipient:
    student_id: int
    student_name: str
    phone: str
    target: RecipientTarget


def normalize_phone(value: str | None) -> str:
    return (value or "").replace("-", "").strip()


def resolve_student_message_recipients(
    tenant,
    student_ids: list[int],
    *,
    send_to: RecipientTarget,
) -> list[StudentMessageRecipient]:
    """
    Resolve active tenant students into recipient candidates.

    Missing/cross-tenant/deleted students are intentionally omitted, matching
    the legacy manual send/preview behavior while making the active-student
    boundary explicit.
    """
    if send_to not in ("student", "parent"):
        raise ValueError(f"unsupported recipient target: {send_to!r}")
    if not student_ids:
        return []

    unique_ids = list(dict.fromkeys(int(student_id) for student_id in student_ids))
    students_by_id = {
        student.id: student
        for student in students_for_tenant(tenant, deleted="active")
        .filter(id__in=unique_ids)
        .only("id", "name", "phone", "parent_phone")
    }

    recipients: list[StudentMessageRecipient] = []
    for student_id in unique_ids:
        student = students_by_id.get(student_id)
        if not student:
            continue
        phone = normalize_phone(student.phone if send_to == "student" else student.parent_phone)
        recipients.append(
            StudentMessageRecipient(
                student_id=student.id,
                student_name=(student.name or "").strip(),
                phone=phone,
                target=send_to,
            )
        )
    return recipients
