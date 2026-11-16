"""Read the assignments of selected video make-up students for a notice."""

import json

from django.utils.crypto import salted_hmac

from apps.domains.homework.models import HomeworkAssignment
from apps.domains.messaging.alimtalk_content_builders import build_manual_replacements, render_alimtalk_preview_text
from apps.support.attendance.learning_todo_eligibility import attendance_status_map

NOTICE = "이번 차시는 영상으로 보강합니다. 아래 과제를 확인해 주세요."
PROVIDER_MAX_MESSAGE_LENGTH = 1000
VIDEO_HOMEWORK_LENGTH_CODE = "video_makeup_homework_message_too_long"


def video_makeup_homework_length_issue(*, recipients, template_type, replacements):
    """Reject oversized provider payloads without omitting assignment titles."""
    too_long = sum(
        len(render_alimtalk_preview_text(template_type, replacements[recipient.student_id])) > PROVIDER_MAX_MESSAGE_LENGTH
        for recipient in recipients
        if len(recipient.phone or "") >= 10 and recipient.student_id in replacements
    )
    if too_long:
        return (
            f"영상보강 과제 안내 {too_long}건이 알림톡 {PROVIDER_MAX_MESSAGE_LENGTH:,}자 제한을 초과합니다. "
            "해당 차시의 과제명을 줄인 뒤 미리보기를 다시 확인해 주세요."
        )
    return None


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
        per_student[str(student_id)] = {"_body_subst": "\n".join([NOTICE, *(f"• {title}" for title in titles)])}

    data["student_ids"] = [recipient["student_id"] for recipient in recipients]
    data["raw_body"] = NOTICE
    data["alimtalk_extra_vars_per_student"] = per_student
    data["_video_makeup_homework"] = {
        "recipients": recipients,
        "excluded": excluded,
        "assignments": homework_by_student,
    }


def build_video_makeup_homework_envelope(*, tenant, data, recipients, template_type, template_id):
    """Prepare the same reviewed replacements for preview and outbox admission."""
    selection = data.get("_video_makeup_homework")
    if selection is None:
        return None, {}
    from apps.domains.messaging.services import get_tenant_site_url

    replacements = {}
    fingerprint = None
    if (
        template_type and template_id and selection["recipients"]
        and set(data["student_ids"]) == {recipient.student_id for recipient in recipients}
    ):
        site_url = get_tenant_site_url(tenant) or ""
        tenant_name = (tenant.name or "").strip()
        for recipient in recipients:
            context = dict(data["alimtalk_extra_vars_per_student"][str(recipient.student_id)])
            body = context.pop("_body_subst")
            replacements[recipient.student_id] = build_manual_replacements(
                template_type=template_type,
                content_body=body,
                context={**data["alimtalk_extra_vars"], **context},
                tenant_name=tenant_name,
                student_name=recipient.student_name,
                site_url=site_url,
                literal_body=True,
            )
        scheduled_send_at = data.get("scheduled_send_at")
        snapshot = {
            "tenant": tenant.pk,
            "session": data["session_id"],
            "route": data["send_to"],
            "scheduled_send_at": scheduled_send_at.isoformat() if scheduled_send_at else None,
            "excluded": selection["excluded"],
            "assignments": selection["assignments"],
            "contacts": [(r.student_id, r.student_name, r.phone) for r in recipients],
            "envelope": [template_type, template_id],
            "replacements": replacements,
            "assignment_order": selection["recipients"],
        }
        serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
        fingerprint = salted_hmac("messaging.video_makeup_homework", serialized, algorithm="sha256").hexdigest()
    return {
        "recipients": selection["recipients"],
        "excluded": selection["excluded"],
        "fingerprint": fingerprint,
    }, replacements
