"""Resolve class-relative message blocks from actual tenant-owned sessions."""
import re
from copy import deepcopy
from django.db.models import Q
from django.utils import timezone
from apps.domains.lectures.models import Session
from apps.domains.enrollment.models import Enrollment
from apps.support.results.progress_read_dependencies import session_score_enrollment_ids

TOKEN = re.compile(r"#\{(현재차시명|현재차시날짜|다음차시명|다음차시날짜|[1-9]\d?차시후명|[1-9]\d?차시후날짜)\}")


class SessionMessageContextError(ValueError):
    pass


def resolve_session_message_data(*, tenant, data):
    """Return a copy. Caller context never overrides server-owned class facts."""
    result = deepcopy(data)
    body = result.get("raw_body") or ""
    if not body and result.get("template_id"):
        from apps.domains.messaging.models import MessageTemplate
        body = MessageTemplate.objects.filter(tenant=tenant, id=result["template_id"]).values_list("body", flat=True).first() or ""
    source_texts = [body] + [str(v.get("_body_subst") or "") for v in (result.get("alimtalk_extra_vars_per_student") or {}).values()]
    names = set(TOKEN.findall("\n".join(source_texts)))
    session_id = result.get("session_id")
    if names and not session_id:
        raise SessionMessageContextError("수업 정보를 넣으려면 차시 성적 화면에서 발송을 열어 주세요.")
    if not session_id:
        if result.get("block_category") == "grades":
            send_at = result.get("scheduled_send_at") or timezone.now()
            values = {"날짜": timezone.localtime(send_at).date().isoformat(), "시간": timezone.localtime(send_at).strftime("%H:%M")}
            result["alimtalk_extra_vars"] = {**(result.get("alimtalk_extra_vars") or {}), **values}
            for context in (result.get("alimtalk_extra_vars_per_student") or {}).values():
                context.update(values)
                if isinstance(context.get("_body_subst"), str):
                    for name, value in values.items():
                        context["_body_subst"] = context["_body_subst"].replace(f"#{{{name}}}", value)
        return result
    session = Session.objects.select_related("lecture", "section").filter(id=session_id, lecture__tenant=tenant).first()
    if session is None:
        raise SessionMessageContextError("선택한 수업을 확인할 수 없습니다. 차시 성적 화면을 다시 열어 주세요.")
    if session.section_id and (session.section.tenant_id != tenant.id or session.section.lecture_id != session.lecture_id):
        raise SessionMessageContextError("선택한 수업의 반 정보를 확인할 수 없습니다. 차시 설정을 확인해 주세요.")
    student_ids = set(result.get("student_ids") or [])
    allowed_ids = set(Enrollment.objects.filter(
        tenant=tenant, lecture=session.lecture, status="ACTIVE", student_id__in=student_ids,
        student__tenant=tenant, student__deleted_at__isnull=True,
        id__in=session_score_enrollment_ids(tenant=tenant, session=session),
    ).values_list("student_id", flat=True))
    if student_ids - allowed_ids:
        raise SessionMessageContextError("선택한 수업에 등록되지 않은 학생이 있습니다. 발송 대상을 다시 선택해 주세요.")
    future = list(Session.objects.filter(lecture=session.lecture, lecture__tenant=tenant, section_id=session.section_id)
                  .filter(Q(order__gt=session.order) | Q(order=session.order, id__gte=session.id)).order_by("order", "id")[:100])
    values = {"차시명": session.title, "현재차시명": session.title, "강의명": session.lecture.title}
    if session.date:
        values.update({"현재차시날짜": session.date.isoformat(), "강의날짜": session.date.isoformat()})
    requested = names.copy()
    if "#{강의날짜}" in body:
        requested.add("현재차시날짜")
    for name in requested:
        offset = 0 if name.startswith("현재") else 1 if name.startswith("다음") else int(re.match(r"\d+", name).group())
        if offset >= len(future):
            raise SessionMessageContextError(f"{offset}차시 뒤 수업이 없습니다. 같은 강의·반에 차시를 등록하거나 해당 수업 블록을 지워 주세요.")
        target = future[offset]
        if name.endswith("날짜"):
            if not target.date:
                raise SessionMessageContextError(f"‘{target.title}’ 수업 날짜가 비어 있습니다. 차시 설정에서 날짜를 등록하거나 날짜 블록을 지워 주세요.")
            values[name] = target.date.isoformat()
        else:
            values[name] = target.title
    send_at = result.get("scheduled_send_at") or timezone.now()
    values.update({"날짜": timezone.localtime(send_at).date().isoformat(), "시간": timezone.localtime(send_at).strftime("%H:%M")})
    result["alimtalk_extra_vars"] = {**(result.get("alimtalk_extra_vars") or {}), **values}
    for context in (result.get("alimtalk_extra_vars_per_student") or {}).values():
        context.update(values)
        if isinstance(context.get("_body_subst"), str):
            text = context["_body_subst"]
            for name, value in values.items():
                text = text.replace(f"#{{{name}}}", value)
            context["_body_subst"] = text
    return result
