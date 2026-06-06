from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

from django.utils import timezone
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

_E2E_MARKER_RE = re.compile(r"\[E2E(?:-[^\]]+)?\]", re.IGNORECASE)


@dataclass(frozen=True)
class _Recipient:
    phone: str
    name: str
    target_type: str
    target_id: int | str


def notify_qna_created(post, *, actor_user=None) -> int:
    """Send a best-effort Alimtalk to tenant staff when a student QnA is created."""
    if getattr(post, "post_type", "") != "qna" or not getattr(post, "created_by_id", None):
        return 0
    if should_suppress_qna_notification(post):
        logger.info("qna created alimtalk suppressed for E2E post=%s", getattr(post, "id", None))
        return 0

    student_name = _post_student_name(post)
    body = _qna_created_body(post)
    return _send_qna_alimtalk_to_recipients(
        tenant=getattr(post, "tenant", None),
        recipients=_iter_staff_recipients(getattr(post, "tenant", None)),
        body=body,
        student_name=student_name,
        category_label=_clean(getattr(post, "category_label", "") or "QnA"),
        action_label="새 질문",
        event_type="qna_created",
        occurrence_key=f"post:{post.id}:created",
        source_use_case="qna_created",
        domain_object_id=str(post.id),
        actor_id=getattr(actor_user, "id", None),
    )


def notify_qna_answered(post, reply, *, send_to: str = "student", actor_user=None) -> int:
    """Send a best-effort Alimtalk to the original QnA author after a staff answer."""
    if getattr(post, "post_type", "") != "qna" or not getattr(post, "created_by_id", None):
        return 0
    if should_suppress_qna_notification(post):
        logger.info(
            "qna answer alimtalk suppressed for E2E post=%s send_to=%s",
            getattr(post, "id", None),
            send_to,
        )
        return 0

    student = getattr(post, "created_by", None)
    if not student:
        return 0

    phone = _normalize_phone(
        getattr(student, "parent_phone", "") if send_to == "parent" else getattr(student, "phone", "")
    )
    if not _is_valid_phone(phone):
        logger.info(
            "qna answer alimtalk skipped: post=%s send_to=%s no valid phone",
            getattr(post, "id", None),
            send_to,
        )
        return 0

    recipient = _Recipient(
        phone=phone,
        name=(getattr(student, "name", "") or "").strip() or "학생",
        target_type="parent" if send_to == "parent" else "student",
        target_id=getattr(student, "id", ""),
    )
    student_name = _post_student_name(post)
    body = _qna_answered_body(post)
    return _send_qna_alimtalk_to_recipients(
        tenant=getattr(post, "tenant", None),
        recipients=[recipient],
        body=body,
        student_name=student_name,
        category_label=_clean(getattr(post, "category_label", "") or "QnA"),
        action_label="답변 등록",
        event_type="qna_answered",
        occurrence_key=f"reply:{getattr(reply, 'id', '')}:answered:{send_to}",
        source_use_case="qna_answered",
        domain_object_id=str(getattr(reply, "id", "") or getattr(post, "id", "")),
        actor_id=getattr(actor_user, "id", None),
    )


def _send_qna_alimtalk_to_recipients(
    *,
    tenant,
    recipients: Iterable[_Recipient],
    body: str,
    student_name: str,
    category_label: str,
    action_label: str,
    event_type: str,
    occurrence_key: str,
    source_use_case: str,
    domain_object_id: str,
    actor_id: int | str | None,
) -> int:
    if not tenant or not body.strip():
        return 0

    from apps.domains.messaging.selectors import resolve_freeform_template
    from apps.domains.messaging.services import enqueue_sms
    from apps.domains.messaging.services.url_helpers import get_tenant_site_url

    template = resolve_freeform_template(tenant.id)
    template_id = (getattr(template, "solapi_template_id", "") or "").strip() if template else ""

    academy_name = (getattr(tenant, "name", "") or "").strip()
    site_url = get_tenant_site_url(tenant) or ""
    sender = ""

    sent = 0
    for recipient in recipients:
        if not _is_valid_phone(recipient.phone):
            continue
        resolved_template_id, replacements, text = _resolve_qna_alimtalk_payload(
            template=template,
            template_id=template_id,
            body=body,
            academy_name=academy_name,
            recipient_name=recipient.name,
            student_name=student_name,
            site_url=site_url,
            category_label=category_label,
            action_label=action_label,
        )
        try:
            ok = enqueue_sms(
                tenant_id=tenant.id,
                to=recipient.phone,
                text=text,
                sender=sender,
                message_mode="alimtalk",
                template_id=resolved_template_id,
                alimtalk_replacements=replacements,
                event_type=event_type,
                target_type=recipient.target_type,
                target_id=recipient.target_id,
                target_name=recipient.name,
                occurrence_key=occurrence_key,
                source_domain="community",
                source_use_case=source_use_case,
                domain_object_id=domain_object_id,
                actor_id=actor_id,
            )
        except Exception as exc:
            logger.warning(
                "qna alimtalk enqueue failed: tenant=%s event=%s target=%s err=%s",
                tenant.id,
                event_type,
                recipient.target_id,
                exc,
            )
            continue
        if ok:
            sent += 1
    return sent


def should_suppress_qna_notification(post) -> bool:
    """External Alimtalk must not fire for production E2E probe content."""
    if not post:
        return False
    for attr in ("title", "content"):
        value = _clean(getattr(post, attr, "") or "")
        if value and _E2E_MARKER_RE.search(value):
            return True
    return False


def _resolve_qna_alimtalk_payload(
    *,
    template,
    template_id: str,
    body: str,
    academy_name: str,
    recipient_name: str,
    student_name: str,
    site_url: str,
    category_label: str,
    action_label: str,
) -> tuple[str, list[dict[str, str]], str]:
    if template_id:
        replacements = _freeform_replacements(
            body=body,
            academy_name=academy_name,
            recipient_name=recipient_name,
            site_url=site_url,
        )
        text = _render_template_text(
            getattr(template, "body", "") or "#{공지내용}",
            replacements,
            subject=getattr(template, "subject", "") or "",
        )
        return template_id, replacements, text

    from apps.domains.messaging.alimtalk_content_builders import (
        SOLAPI_ATTENDANCE,
        TYPE_ATTENDANCE,
        build_manual_replacements,
    )

    now = timezone.localtime()
    replacements = build_manual_replacements(
        TYPE_ATTENDANCE,
        body,
        {
            "강의명": _clip(category_label or "QnA", 23),
            "차시명": _clip(action_label or "QnA", 23),
            "날짜": now.strftime("%m/%d"),
            "시간": now.strftime("%H:%M"),
        },
        tenant_name=academy_name,
        student_name=student_name or recipient_name,
        site_url=site_url,
    )
    return SOLAPI_ATTENDANCE, replacements, body


def _iter_staff_recipients(tenant) -> list[_Recipient]:
    if not tenant:
        return []

    from apps.core.models import TenantMembership
    from apps.domains.staffs.models import Staff

    recipients: list[_Recipient] = []
    seen_phones: set[str] = set()

    # 2026-05-30: 박철과학 학원장 directive — QnA 알림은 원장(owner)에게만.
    # 조교(teacher)는 퇴근 후 일반인이라 알림이 가면 안 된다. admin/staff 도
    # 우선 제외 — 원장이 admin 추가 수신 원하면 별도 라운드에서 확장.
    memberships = (
        TenantMembership.objects.filter(
            tenant=tenant,
            is_active=True,
            role__in=["owner"],
        )
        .select_related("user", "user__staff_profile")
        .only(
            "user__id",
            "user__name",
            "user__username",
            "user__phone",
            "user__staff_profile__name",
            "user__staff_profile__phone",
        )
    )
    for membership in memberships:
        user = getattr(membership, "user", None)
        if not user:
            continue
        staff_profile = getattr(user, "staff_profile", None)
        phone = _normalize_phone(getattr(user, "phone", None) or getattr(staff_profile, "phone", None))
        if not _is_valid_phone(phone) or phone in seen_phones:
            continue
        seen_phones.add(phone)
        name = (
            getattr(user, "name", None)
            or getattr(staff_profile, "name", None)
            or getattr(user, "username", None)
            or "선생님"
        )
        recipients.append(
            _Recipient(
                phone=phone,
                name=str(name).strip() or "선생님",
                target_type="teacher",
                target_id=getattr(user, "id", phone),
            )
        )

    staff_qs = Staff.objects.filter(tenant=tenant, is_active=True).only("id", "name", "phone")
    for staff in staff_qs:
        phone = _normalize_phone(getattr(staff, "phone", None))
        if not _is_valid_phone(phone) or phone in seen_phones:
            continue
        seen_phones.add(phone)
        recipients.append(
            _Recipient(
                phone=phone,
                name=(getattr(staff, "name", "") or "").strip() or "선생님",
                target_type="teacher",
                target_id=getattr(staff, "id", phone),
            )
        )

    return recipients


def _post_student_name(post) -> str:
    return _clean(getattr(getattr(post, "created_by", None), "name", "") or getattr(post, "author_display_name", ""))


def _qna_created_body(post) -> str:
    student_name = _post_student_name(post)
    title = _clean(getattr(post, "title", ""))
    category = _clean(getattr(post, "category_label", "") or "강의 선택 없음")
    return "\n".join(
        [
            "[QnA 새 질문]",
            f"{student_name or '학생'} 학생이 질문을 등록했습니다.",
            f"제목: {_clip(title, 80)}",
            f"강의: {_clip(category, 40)}",
            "선생앱 > 소통 > Q&A에서 확인해 주세요.",
        ]
    )


def _qna_answered_body(post) -> str:
    student_name = _post_student_name(post)
    title = _clean(getattr(post, "title", ""))
    return "\n".join(
        [
            "[QnA 답변 등록]",
            f"{student_name or '학생'} 학생 질문에 답변이 등록되었습니다.",
            f"제목: {_clip(title, 80)}",
            "학생앱 > 커뮤니티 > QnA에서 확인해 주세요.",
        ]
    )


def _freeform_replacements(*, body: str, academy_name: str, recipient_name: str, site_url: str) -> list[dict[str, str]]:
    name = recipient_name.strip()
    name_2 = name[-2:] if len(name) >= 2 else name
    return [
        {"key": "공지내용", "value": body},
        {"key": "내용", "value": body},
        {"key": "선생님메모", "value": body},
        {"key": "학원명", "value": academy_name},
        {"key": "학원이름", "value": academy_name},
        {"key": "학생이름", "value": name},
        {"key": "학생이름2", "value": name_2},
        {"key": "학생이름3", "value": name},
        {"key": "사이트링크", "value": site_url},
    ]


def _render_template_text(template_body: str, replacements: list[dict[str, str]], *, subject: str = "") -> str:
    text = template_body or "#{공지내용}"
    for replacement in replacements:
        text = text.replace(f"#{{{replacement['key']}}}", str(replacement["value"]))
    text = re.sub(r"#\{[^}]+\}", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if subject:
        return f"{subject.strip()}\n{text}".strip()
    return text


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", strip_tags(str(value or ""))).strip()


def _clip(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "..."


def _normalize_phone(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _is_valid_phone(value: str) -> bool:
    return bool(value and len(value) >= 10)
