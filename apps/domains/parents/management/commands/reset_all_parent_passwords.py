# PATH: apps/domains/parents/management/commands/reset_all_parent_passwords.py
"""
한 학원의 학부모 계정 비밀번호를 학부모 전화번호 마지막 4자리로 초기화.
must_change_password=True 도 함께 설정 — 첫 로그인 시 비밀번호 변경 강제.

사용:
  python manage.py reset_all_parent_passwords --tenant-code=<code>
  python manage.py reset_all_parent_passwords --tenant-code=<code> --apply --confirm-count=<count>
"""
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.models import Tenant
from apps.core.services.password import force_reset_password
from apps.domains.parents.models import Parent
from apps.domains.parents.services import parent_initial_password


class Command(BaseCommand):
    help = "한 학원의 학부모 비밀번호를 전화번호 마지막 4자리로 초기화"

    def add_arguments(self, parser):
        parser.add_argument("--tenant-code", required=True, help="대상 학원 코드")
        parser.add_argument("--apply", action="store_true", help="검증한 대상을 실제 초기화")
        parser.add_argument("--confirm-count", type=int, help="dry-run에서 확인한 정확한 대상 수")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="호환 옵션. --apply를 생략하면 항상 dry-run",
        )

    def handle(self, *args, **options):
        if options["apply"] and options["dry_run"]:
            raise CommandError("--apply와 --dry-run은 함께 사용할 수 없습니다.")

        tenant_code = str(options["tenant_code"] or "").strip()
        tenant = Tenant.objects.filter(code__iexact=tenant_code).first()
        if tenant is None:
            raise CommandError(f"학원을 찾을 수 없습니다: {tenant_code}")

        parents_with_user = Parent.objects.filter(
            tenant=tenant,
            user__isnull=False,
        ).select_related("user", "tenant")

        count = parents_with_user.count()
        self.stdout.write(f"대상 학원: {tenant.code} (tenant_id={tenant.id})")
        self.stdout.write(f"대상 학부모 계정: {count}명")

        if count == 0:
            self.stdout.write(self.style.SUCCESS("대상 없음."))
            return

        for p in parents_with_user[:10]:
            self.stdout.write(
                f"  parent_id={p.id} phone={p.phone[:3]}********"
            )
        if count > 10:
            self.stdout.write(f"  ... 외 {count - 10}명")

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("dry-run: 실제 변경 없음."))
            return

        if options["confirm_count"] != count:
            raise CommandError(
                f"--confirm-count가 현재 대상 수와 일치해야 합니다: expected={count}"
            )

        # 학부모마다 phone 마지막 4자리가 다르므로 행 단위 처리한다.
        # canonical reset service가 기존 토큰과 pending reset도 함께 폐기한다.
        updated = 0
        with transaction.atomic():
            for p in parents_with_user.iterator():
                user = p.user
                if not user:
                    continue
                force_reset_password(user, parent_initial_password(p.phone))
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"완료: {updated}명 학부모 비밀번호 → 전화번호 뒤 4자리, must_change_password=True"
            )
        )
