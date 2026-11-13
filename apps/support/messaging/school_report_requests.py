"""Server-owned student and term context for manual school report Alimtalk."""

import re
from urllib.parse import urlencode

from django.conf import settings
from rest_framework.exceptions import ValidationError

from apps.domains.messaging.alimtalk_content_builders import TEMPLATE_TYPE_TO_SOLAPI_ID
from apps.domains.messaging.models import AutoSendConfig
from apps.domains.messaging.permissions import can_send_messages
from apps.domains.messaging.policy import (
    SCHOOL_REPORT_REQUEST_TRIGGER,
    get_owner_tenant_id,
    is_event_dry_run,
    is_messaging_disabled,
    is_messaging_restricted,
    is_messaging_runtime_held,
    resolve_kakao_channel,
)
from apps.domains.messaging.selectors import get_auto_send_config
from apps.domains.messaging.services.recipients import resolve_student_message_recipients
from apps.domains.messaging.tenant_channels import TenantAlimtalkRouteError, resolve_alimtalk_delivery_route
from apps.support.results.school_reports import SchoolReportTerm, missing_student_accounts


TERM_VARIABLES = frozenset({"학년도", "학기", "시험회차", "시험명"})
ALLOWED_VARIABLES = TERM_VARIABLES | {"학원명", "학생이름", "학생이름2", "사이트링크"}


def _primary_host(tenant):
    return tenant.domains.filter(is_primary=True, is_active=True).values_list("host", flat=True).first()


def _term_url(host, term):
    return f"https://{host}/student/submit/school-report?{urlencode(term.as_dict())}"


def school_report_alimtalk_template(tenant):
    owner_id = get_owner_tenant_id()
    if (
        is_messaging_disabled(tenant.id) or is_messaging_restricted(tenant.id)
        or is_messaging_runtime_held(owner_id) or is_event_dry_run(SCHOOL_REPORT_REQUEST_TRIGGER)
    ):
        return None
    tenant_config = get_auto_send_config(tenant.id, SCHOOL_REPORT_REQUEST_TRIGGER)
    if not tenant_config or tenant_config.message_mode != "alimtalk":
        return None
    if tenant_config.template_id and tenant_config.template.tenant_id != tenant.id:
        return None
    owner_config = tenant_config if tenant.id == owner_id else get_auto_send_config(owner_id, SCHOOL_REPORT_REQUEST_TRIGGER)
    template = owner_config.template if owner_config else None
    if not template or template.tenant_id != owner_id or template.solapi_status != "APPROVED":
        return None
    sid = (template.solapi_template_id or "").strip()
    body = (template.body or "").strip()
    variables = set(re.findall(r"#\{([^}]+)\}", body))
    if (
        not sid or sid in TEMPLATE_TYPE_TO_SOLAPI_ID.values() or not body
        or not variables <= ALLOWED_VARIABLES or "사이트링크" not in variables
        or not ("시험명" in variables or {"학년도", "학기", "시험회차"} <= variables)
        or AutoSendConfig.objects.filter(tenant_id=owner_id, template__solapi_template_id=sid)
        .exclude(trigger=SCHOOL_REPORT_REQUEST_TRIGGER).exists()
    ):
        return None
    # Only the Academy owner binds this dedicated approved template; a tenant
    # config does not substitute another template or another recipient channel.
    if not (
        settings.SOLAPI_API_KEY and settings.SOLAPI_API_SECRET and settings.SOLAPI_SENDER
        and _primary_host(tenant)
    ):
        return None
    common_channel = resolve_kakao_channel(owner_id).get("pf_id") or ""
    try:
        resolve_alimtalk_delivery_route(
            source_tenant_id=tenant.id,
            common_channel_id=common_channel,
            common_template_id=sid,
        )
    except TenantAlimtalkRouteError:
        return None
    return template


def school_report_alimtalk_available(request, tenant):
    return can_send_messages(request, tenant) and school_report_alimtalk_template(tenant) is not None


def parse_term(values):
    year = values.get("academic_year")
    semester = values.get("semester")
    exam_round = values.get("exam_round")
    if (
        type(year) is not int or not 2000 <= year <= 2100
        or type(semester) is not int or semester not in (1, 2)
        or exam_round not in ("first", "second")
    ):
        raise ValueError("학년도, 학기, 시험 회차를 다시 선택해 주세요.")
    return SchoolReportTerm(year, semester, exam_round)


def school_report_preview_context(tenant, source):
    term = parse_term(source)
    student_ids = source.get("student_ids")
    if (
        not isinstance(student_ids, list) or not 1 <= len(student_ids) <= 100
        or any(type(student_id) is not int or not 1 <= student_id <= 2**63 - 1 for student_id in student_ids)
    ):
        raise ValueError("요청 대상은 1~100명의 학생을 선택해 주세요.")
    student_ids = list(dict.fromkeys(student_ids))
    eligible = missing_student_accounts(tenant, term, student_ids)
    if len(eligible) != len(student_ids):
        raise ValueError("이미 제출했거나 활성 계정이 아닌 학생을 제외하고 다시 선택해 주세요.")
    primary_host = _primary_host(tenant)
    if not primary_host:
        raise ValueError("학생 앱 주소를 확인할 수 없습니다.")
    round_name = "중간고사" if term.exam_round == "first" else "기말고사"
    return term, student_ids, {
        "학년도": term.academic_year,
        "학기": term.semester,
        "시험회차": round_name,
        "시험명": f"{term.academic_year}학년도 {term.semester}학기 {round_name}",
        "사이트링크": _term_url(primary_host, term),
    }


def school_report_confirmation_valid(tenant, payload):
    source = payload.get("school_report_source")
    if (
        not isinstance(source, dict) or payload.get("send_to") != "student"
        or payload.get("notification_type") != SCHOOL_REPORT_REQUEST_TRIGGER
        or payload.get("message_mode") != "alimtalk"
    ):
        return False
    template = school_report_alimtalk_template(tenant)
    if (
        not template
        or template.solapi_template_id.strip() != payload.get("solapi_template_id")
        or template.body.strip() != payload.get("message_template_body")
    ):
        return False
    try:
        term = parse_term(source)
        host = _primary_host(tenant)
        if not host:
            return False
        for recipient in payload["recipients"]:
            variables = {item["key"]: item["value"] for item in recipient["alimtalk_replacements"]}
            if variables.get("사이트링크") != _term_url(host, term):
                return False
        selected = {
            row["student_id"]: (row["phone_raw"], row["student_name"])
            for row in payload["recipients"]
        }
        eligible = missing_student_accounts(tenant, term, list(selected), lock=True)
    except (KeyError, TypeError, ValueError, ValidationError):
        return False
    actual = {
        row.student_id: (row.phone, row.student_name)
        for row in resolve_student_message_recipients(tenant, list(eligible), send_to="student")
    }
    return bool(selected) and actual == selected
