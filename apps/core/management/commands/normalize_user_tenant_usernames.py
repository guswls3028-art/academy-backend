# PATH: apps/core/management/commands/normalize_user_tenant_usernames.py
"""
테넌트 소속 User의 username을 t{tenant_id}_{display} 형식으로 정규화.
격리 작업 전 생성된 레거시 유저(username에 접두어 없음)를 현재 규칙에 맞게 한 번만 반영.

- 이미 t{id}_ 로 시작하면 스킵.
- tenant가 있는데 접두어 없으면 → username을 t{tenant_id}_{기존username} 로 변경 (전역 유일 확인 후).

사용:
  python manage.py normalize_user_tenant_usernames           # dry-run
  python manage.py normalize_user_tenant_usernames --apply  # 실제 저장
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.contrib.auth import get_user_model

from apps.core.models.user import USERNAME_TENANT_PREFIX

User = get_user_model()


class Command(BaseCommand):
    help = "Normalize tenant User usernames to t{tenant_id}_{display} (legacy after tenant isolation)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply changes. Default is dry-run.",
        )

    def handle(self, *args, **options):
        apply = options.get("apply", False)
        if not apply:
            self.stdout.write(self.style.WARNING("Dry-run. Use --apply to save."))

        qs = User.objects.filter(tenant__isnull=False).select_related("tenant")
        updated = 0
        skipped = 0
        errors = []

        for user in qs:
            uname = (user.username or "").strip()
            if not uname:
                continue
            prefix = f"{USERNAME_TENANT_PREFIX}{user.tenant_id}_"
            if uname.startswith(prefix):
                skipped += 1
                continue
            new_username = f"{prefix}{uname}"
            if User.objects.filter(username=new_username).exclude(pk=user.pk).exists():
                errors.append(f"user_id={user.pk} tenant={user.tenant_id} conflict: {new_username!r} already exists")
                continue
            if apply:
                with transaction.atomic():
                    user.username = new_username
                    user.save(update_fields=["username"])
                self.stdout.write(f"  user_id={user.pk} tenant={user.tenant.code} -> {new_username!r}")
                updated += 1
            else:
                self.stdout.write(f"  [would] user_id={user.pk} {uname!r} -> {new_username!r}")
                updated += 1

        for err in errors:
            self.stderr.write(self.style.ERROR(err))
        self.stdout.write(
            self.style.SUCCESS(f"Done. updated={updated} skipped={skipped} errors={len(errors)}" + (" (applied)" if apply else " (dry-run)"))
        )
