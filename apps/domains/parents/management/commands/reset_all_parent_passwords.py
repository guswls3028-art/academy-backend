# PATH: apps/domains/parents/management/commands/reset_all_parent_passwords.py
"""
모든 학부모 계정 비밀번호를 "0000"으로 초기화.

사용:
  python manage.py reset_all_parent_passwords --dry-run    # 대상만 확인
  python manage.py reset_all_parent_passwords              # 실행
"""
from django.core.management.base import BaseCommand
from django.contrib.auth.hashers import make_password

from apps.domains.parents.models import Parent
from apps.domains.parents.services import PARENT_DEFAULT_PASSWORD


class Command(BaseCommand):
    help = "모든 학부모 계정 비밀번호를 0000으로 초기화"

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

        # 일괄 업데이트 (해시 한 번만 생성)
        hashed = make_password(PARENT_DEFAULT_PASSWORD)
        from django.contrib.auth import get_user_model
        User = get_user_model()

        user_ids = list(parents_with_user.values_list("user_id", flat=True))
        updated = User.objects.filter(id__in=user_ids).update(password=hashed)

        self.stdout.write(self.style.SUCCESS(f"완료: {updated}명 학부모 비밀번호 → 0000 초기화"))
