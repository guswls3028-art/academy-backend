"""Django adapter for matchup notification side effects."""

from __future__ import annotations

import logging

from apps.core.models import TenantMembership
from apps.core.models.user import user_display_username
from apps.domains.messaging.alimtalk_content_builders import (
    build_unified_replacements,
    get_solapi_template_id,
)
from apps.domains.messaging.policy import is_messaging_disabled
from apps.domains.messaging.selectors import get_auto_send_config
from apps.domains.messaging.services import enqueue_sms


logger = logging.getLogger(__name__)


def notify_hit_report_submitted(report, _request=None) -> None:
    """Send owner/admin notification when a matchup hit report is submitted."""

    trigger = "matchup_report_submitted"
    tenant = report.tenant
    tenant_id = tenant.id

    if is_messaging_disabled(tenant_id):
        logger.info("hit_report_notify skipped: tenant %s messaging disabled", tenant_id)
        return

    config = get_auto_send_config(tenant_id, trigger)
    if not config or not config.enabled:
        logger.debug(
            "hit_report_notify skipped: trigger=%s tenant=%s (config disabled or missing)",
            trigger, tenant_id,
        )
        return

    template = config.template
    template_body = (template.body if template else "") or (
        "강사가 매치업 적중 보고서를 제출했습니다.\n"
        "어드민 → 매치업에서 보고서 inbox를 확인해 주세요."
    )

    tenant_name = (tenant.name or "").strip() or "학원"
    site_url = "https://hakwonplus.com"
    if tenant.code:
        site_url = f"https://{tenant.code}.hakwonplus.com"

    author_name = ""
    if report.author_id and report.author is not None:
        author_name = (
            getattr(report.author, "name", None)
            or user_display_username(report.author)
            or ""
        )
    if not author_name:
        author_name = report.submitted_by_name or "강사"

    doc = report.document
    doc_title = (doc.title if doc else "") or "시험지"
    doc_category = (doc.category if doc else "") or ""

    context = {
        "강의명": (doc_category or doc_title)[:30],
        "차시명": f"{doc_title[:20]}  ·  {author_name} 강사"[:30],
    }

    memberships = list(
        TenantMembership.objects.filter(
            tenant=tenant, is_active=True, role__in=["owner", "admin"],
        ).select_related("user").only(
            "user__id", "user__name", "user__username", "user__phone",
        )
    )
    if not memberships:
        logger.info("hit_report_notify: no owner/admin in tenant %s", tenant_id)
        return

    if report.author_id:
        non_author_recipients = [
            m for m in memberships
            if getattr(getattr(m, "user", None), "id", None) != report.author_id
        ]
        if not non_author_recipients:
            logger.info(
                "hit_report_notify suppressed: solo academy (author=%s sole owner/admin), tenant=%s",
                report.author_id, tenant_id,
            )
            return
        memberships = non_author_recipients

    solapi_tid = get_solapi_template_id(trigger)
    if not solapi_tid:
        logger.info(
            "hit_report_notify suppressed: no approved alimtalk template for %s (tenant=%s)",
            trigger, tenant_id,
        )
        return

    sent_count = 0
    sent_user_ids: list[int] = []
    for membership in memberships:
        user = getattr(membership, "user", None)
        if not user:
            continue
        phone = (getattr(user, "phone", "") or "").replace("-", "").strip()
        if not phone:
            logger.debug(
                "hit_report_notify: user %s has no phone, skip", getattr(user, "id", "?"),
            )
            continue

        recipient_name = getattr(user, "name", None) or getattr(user, "username", "") or ""
        replacements = build_unified_replacements(
            trigger=trigger,
            content_body=template_body,
            context=context,
            tenant_name=tenant_name,
            student_name=recipient_name,
            site_url=site_url,
        )

        try:
            ok = enqueue_sms(
                tenant_id=tenant_id,
                to=phone,
                text=template_body,
                message_mode="alimtalk",
                template_id=solapi_tid,
                alimtalk_replacements=replacements,
            )
            if ok:
                sent_count += 1
                sent_user_ids.append(user.id)
        except Exception as exc:
            logger.warning(
                "hit_report_notify enqueue failed: report=%s user=%s err=%s",
                report.id, user.id, exc,
            )

    logger.info(
        "HIT_REPORT_NOTIFIED | tenant=%s report=%s author=%s recipients=%d/%d user_ids=%s",
        tenant_id, report.id, report.author_id, sent_count, len(memberships), sent_user_ids,
    )
