"""Read the assignments of selected video make-up students for a notice."""

import json

from django.utils.crypto import salted_hmac

from apps.domains.homework.models import HomeworkAssignment
from apps.support.attendance.learning_todo_eligibility import attendance_status_map


def resolve_video_makeup_homework(*, tenant, session, data, enrollments):
    """Populate a generic manual send with server-owned recipients and copy."""
    candidate_ids = sorted(set(data["student_ids"]))
    attendance = attendance_status_map(tenant=tenant, session=session, enrollment_ids=enrollments)
    online = {key: value for key, value in enrollments.items() if attendance.get(key) == "ONLINE"}
    assignments = HomeworkAssignment.objects.filter(
        tenant=tenant,
        session=session,
        enrollment_id__in=online,
        homework__tenant=tenant,
        homework__session=session,
        homework__homework_type="regular",
    ).exclude(
        homework__meta__removed_from_session_at__isnull=False,
    ).order_by(
        "homework__display_order", "homework__created_at", "homework_id",
    ).values_list("enrollment_id", "homework_id", "homework__title")

    homework_by_student = {}
    for enrollment_id, homework_id, title in assignments:
        homework_by_student.setdefault(online[enrollment_id], {})[homework_id] = title

    online_student_ids = set(online.values())
    recipients = []
    excluded = []
    per_student = {}
    for student_id in candidate_ids:
        if student_id not in online_student_ids:
            excluded.append({"student_id": student_id, "reason": "not_video"})
            continue
        titles = list(homework_by_student.get(student_id, {}).values())
        if not titles:
            excluded.append({"student_id": student_id, "reason": "no_homework"})
            continue
        recipients.append({"student_id": student_id, "homework_titles": titles})
        per_student[str(student_id)] = {"과제목록": "\n".join(f"• {title}" for title in titles)}

    data["student_ids"] = [recipient["student_id"] for recipient in recipients]
    data["raw_body"] = "이번 차시는 영상으로 보강합니다. 아래 과제를 확인해 주세요.\n#{과제목록}"
    data["alimtalk_extra_vars_per_student"] = per_student
    data["_video_makeup_homework"] = {
        "recipients": recipients,
        "excluded": excluded,
        "assignments": homework_by_student,
    }


def video_makeup_homework_preview(*, tenant, data, recipients):
    selection = data.get("_video_makeup_homework")
    if selection is None:
        return None
    fingerprint = None
    if selection["recipients"] and set(data["student_ids"]) == {recipient.student_id for recipient in recipients}:
        session_context = data["alimtalk_extra_vars"]
        scheduled_send_at = data.get("scheduled_send_at")
        snapshot = {
            "tenant": tenant.pk,
            "session": data["session_id"],
            "route": data["send_to"],
            "scheduled_send_at": scheduled_send_at.isoformat() if scheduled_send_at else None,
            "excluded": selection["excluded"],
            "assignments": selection["assignments"],
            "contacts": [(r.student_id, r.student_name, r.phone) for r in recipients],
            "lecture": session_context.get("강의명"),
            "session_title": session_context.get("차시명"),
            "session_date": session_context.get("강의날짜"),
            "body": data["raw_body"],
            "assignment_order": selection["recipients"],
        }
        serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        fingerprint = salted_hmac("messaging.video_makeup_homework", serialized, algorithm="sha256").hexdigest()
    return {
        "recipients": selection["recipients"],
        "excluded": selection["excluded"],
        "fingerprint": fingerprint,
    }
