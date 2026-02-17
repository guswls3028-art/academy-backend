# PATH: apps/core/management/commands/set_tenant_sender.py
"""
테넌트별 발신번호(messaging_sender) 설정.

사용:
  python manage.py set_tenant_sender --tenant=hakwonplus --sender=01031217466
  python manage.py set_tenant_sender --tenant=1 --sender=01031217466
"""
from django.core.management.base import BaseCommand

from academy.adapters.db.django import repositories_core as core_repo


class Command(BaseCommand):
    help = "Set Tenant.messaging_sender for SMS/알림톡 발신."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", type=str, required=True, help="Tenant code or id (e.g. hakwonplus, 1)")
        parser.add_argument("--sender", type=str, required=True, help="발신번호 (예: 01031217466)")

    def handle(self, *args, **options):
        tenant_arg = str(options["tenant"]).strip()
        sender = (options["sender"] or "").replace("-", "").strip()
        if not sender or len(sender) < 10:
            self.stderr.write(self.style.ERROR("올바른 발신번호를 입력하세요 (예: 01031217466)"))
            return
        if tenant_arg.isdigit():
            tenant = core_repo.tenant_get_by_id_any(int(tenant_arg))
        else:
            tenant = core_repo.tenant_get_by_code(tenant_arg)
        if not tenant:
            self.stderr.write(self.style.ERROR(f"Tenant '{tenant_arg}' not found."))
            return
        old = (tenant.messaging_sender or "").strip()
        tenant.messaging_sender = sender
        tenant.save(update_fields=["messaging_sender"])
        self.stdout.write(
            self.style.SUCCESS(f"Tenant {tenant.code} (id={tenant.id}): messaging_sender {old or '(empty)'} -> {sender}")
        )
