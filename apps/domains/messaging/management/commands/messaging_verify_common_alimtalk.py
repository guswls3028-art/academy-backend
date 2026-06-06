from __future__ import annotations

import re
import time
from uuid import uuid4

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.core.models import Tenant
from apps.domains.messaging.models import NotificationLog
from apps.domains.messaging.policy import get_owner_tenant_id, send_alimtalk_via_owner
from apps.domains.messaging.selectors import get_auto_send_config


CONTROLLED_VERIFY_PHONE = "01031217466"
ACCOUNT_VERIFY_TRIGGERS = (
    "password_reset_student",
    "password_reset_parent",
    "registration_approved_student",
    "registration_approved_parent",
)


def _normalize_phone(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _mask_phone(value: str) -> str:
    return f"{value[:4]}****{value[-2:]}" if len(value) >= 6 else "****"


def _site_url() -> str:
    return getattr(settings, "SITE_URL", "") or "https://hakwonplus.com"


def _template_placeholders(body: str) -> set[str]:
    return {match.strip() for match in re.findall(r"#\{([^}]+)\}", body or "") if match.strip()}


def _build_replacements(
    *,
    template_body: str,
    trigger: str,
    academy_name: str,
    name: str,
    login_id: str,
    password: str,
) -> dict[str, str]:
    now = timezone.localtime()
    defaults = {
        "학생이름": name,
        "학부모이름": f"{name} 학부모",
        "이름": name,
        "학생아이디": login_id,
        "학부모아이디": CONTROLLED_VERIFY_PHONE,
        "아이디": login_id,
        "학생비밀번호": password,
        "학부모비밀번호": password,
        "임시비밀번호": password,
        "비밀번호안내": "운영 검증용 알림톡입니다.",
        "사이트링크": _site_url(),
        "학원명": academy_name or "학원플러스",
        "인증번호": "123456",
        "날짜": now.strftime("%Y-%m-%d"),
        "시간": now.strftime("%H:%M"),
    }
    if trigger.endswith("_parent"):
        defaults["아이디"] = defaults["학부모아이디"]
    replacements = dict(defaults)
    for key in _template_placeholders(template_body):
        replacements.setdefault(key, defaults.get(key, name))
    return replacements


class Command(BaseCommand):
    help = "Send one production verification Alimtalk through the common owner channel only."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source-tenant",
            "--tenant",
            dest="source_tenant_id",
            type=int,
            default=3,
            help="Business/source tenant id to record in NotificationLog (default: 3).",
        )
        parser.add_argument(
            "--phone",
            "--parent-phone",
            dest="phone",
            type=str,
            default=CONTROLLED_VERIFY_PHONE,
            help=f"Controlled verification recipient. Must be {CONTROLLED_VERIFY_PHONE}.",
        )
        parser.add_argument(
            "--trigger",
            choices=ACCOUNT_VERIFY_TRIGGERS,
            default="password_reset_student",
            help="Owner account template trigger to verify.",
        )
        parser.add_argument("--name", type=str, default="", help="Display name for the verification message.")
        parser.add_argument("--temp-password", type=str, default="135790", help="Template variable only.")
        parser.add_argument("--wait-seconds", type=int, default=120, help="Wait for worker NotificationLog result.")

    def handle(self, *args, **options):
        phone = _normalize_phone(options["phone"])
        if phone != CONTROLLED_VERIFY_PHONE:
            raise CommandError(
                f"Production verification may send only to {CONTROLLED_VERIFY_PHONE}; got {_mask_phone(phone)}."
            )

        source_tenant_id = int(options["source_tenant_id"])
        source_tenant = Tenant.objects.filter(pk=source_tenant_id).only("id", "name").first()
        if not source_tenant:
            raise CommandError(f"Source tenant id={source_tenant_id} not found.")

        owner_id = int(get_owner_tenant_id())
        trigger = options["trigger"]
        config = get_auto_send_config(owner_id, trigger)
        template = config.template if config else None
        template_id = (getattr(template, "solapi_template_id", "") or "").strip()
        if not template or not template_id or getattr(template, "solapi_status", "") != "APPROVED":
            raise CommandError(f"Owner template is not approved for trigger={trigger}.")

        tag = timezone.localtime().strftime("E2E-%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
        name = (options["name"] or "").strip() or f"{tag} 계정검증"
        login_id = f"{tag}-login"
        target_id = f"verify:{tag}"
        replacements = _build_replacements(
            template_body=template.body or "",
            trigger=trigger,
            academy_name=source_tenant.name or "",
            name=name,
            login_id=login_id,
            password=str(options["temp_password"] or "135790"),
        )

        ok = send_alimtalk_via_owner(
            trigger=trigger,
            to=phone,
            replacements=replacements,
            source_tenant_id=source_tenant_id,
            log_target_type="account",
            log_target_id=target_id,
            log_target_name=name,
        )
        if not ok:
            raise CommandError("Common owner Alimtalk enqueue failed.")

        self.stdout.write(
            self.style.SUCCESS(
                f"Enqueued common-owner Alimtalk trigger={trigger} source_tenant={source_tenant_id} "
                f"to={_mask_phone(phone)} target_id={target_id} template_id={template_id}"
            )
        )

        wait_seconds = max(0, int(options["wait_seconds"] or 0))
        if wait_seconds <= 0:
            return

        deadline = time.monotonic() + wait_seconds
        last_log = None
        while time.monotonic() <= deadline:
            last_log = (
                NotificationLog.objects
                .filter(
                    tenant_id=owner_id,
                    source_tenant_id=source_tenant_id,
                    target_type="account",
                    target_id=target_id,
                    notification_type=trigger,
                )
                .order_by("-sent_at")
                .first()
            )
            if last_log and last_log.status in ("sent", "failed"):
                break
            time.sleep(3)

        if not last_log:
            raise CommandError(f"Timed out waiting for NotificationLog target_id={target_id}.")

        provider_id = last_log.provider_message_id or ""
        summary = (
            f"NotificationLog id={last_log.id} status={last_log.status} success={last_log.success} "
            f"tenant_id={last_log.tenant_id} source_tenant_id={last_log.source_tenant_id} "
            f"message_mode={last_log.message_mode} provider_message_id={provider_id or '-'} "
            f"failure_reason={last_log.failure_reason or '-'}"
        )
        if last_log.success:
            self.stdout.write(self.style.SUCCESS(summary))
            return
        raise CommandError(summary)
