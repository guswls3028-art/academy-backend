# PATH: apps/domains/parents/management/commands/reset_all_parent_passwords.py
"""
모든 학부모 계정 비밀번호를 학부모 전화번호 마지막 4자리로 초기화.
must_change_password=True 도 함께 설정 — 첫 로그인 시 비밀번호 변경 강제.

사용:
  python manage.py reset_all_parent_passwords --dry-run    # 대상만 확인
  python manage.py reset_all_parent_passwords              # 실행
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.hashers import make_password
from django.db import transaction

from apps.domains.parents.models import Parent
from apps.domains.parents.services import parent_initial_password


class Command(BaseCommand):
    help = "모든 학부모 비밀번호를 전화번호 마지막 4자리로 초기화 + must_change_password=True"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="대상만 출력")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        parents_with_user = Parent.objects.filter(
            user__isnull=False,
        ).select_related("user", "tenant")

        count = parents_with_user.count()
        self.stdout.write(f"대상 학부모 계정: {count}명")

        if count == 0:
            self.stdout.write(self.style.SUCCESS("대상 없음."))
            return

        for p in parents_with_user[:10]:
            self.stdout.write(
                f"  tenant={p.tenant_id} parent_id={p.id} phone={p.phone[:3]}****{p.phone[-4:] if len(p.phone) >= 7 else ''}"
            )
        if count > 10:
            self.stdout.write(f"  ... 외 {count - 10}명")

        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: 실제 변경 없음."))
            return

        # 학부모마다 phone 마지막 4자리가 다르므로 행 단위 업데이트.
        updated = 0
        with transaction.atomic():
            for p in parents_with_user.iterator():
                user = p.user
                if not user:
                    continue
                user.password = make_password(parent_initial_password(p.phone))
                user.must_change_password = True
                user.save(update_fields=["password", "must_change_password"])
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"완료: {updated}명 학부모 비밀번호 → 전화번호 뒤 4자리, must_change_password=True"
            )
        )
