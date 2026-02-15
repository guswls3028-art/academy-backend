# PATH: apps/domains/students/management/commands/check_deleted_student_duplicates.py
"""
삭제된 학생 중 '이름+학부모전화' 중복 검사 및 정리.

버그로 인해 동일 인물이 삭제된 학생에 여러 건 쌓였을 때,
그룹당 1명(가장 오래된 deleted_at)만 남기고 나머지 영구 삭제.

사용:
  python manage.py check_deleted_student_duplicates              # 중복만 검사
  python manage.py check_deleted_student_duplicates --dry-run    # 삭제 대상만 출력
  python manage.py check_deleted_student_duplicates --fix        # 중복 그룹 정리 실행
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from academy.adapters.db.django import repositories_students as student_repo


class Command(BaseCommand):
    help = "삭제된 학생 중 (tenant, 이름, 학부모전화) 중복 검사 및 정리 (--fix 시 그룹당 1명만 유지)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="삭제 대상만 출력, 실제 삭제/정리 없음",
        )
        parser.add_argument(
            "--fix",
            action="store_true",
            help="중복 그룹에서 1명(가장 오래된 deleted_at)만 남기고 나머지 영구 삭제",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        do_fix = options["fix"]

        dup_groups = student_repo.student_filter_deleted_dup_groups()
        groups_list = list(dup_groups)
        if not groups_list:
            self.stdout.write(self.style.SUCCESS("삭제된 학생 중 (이름+학부모전화) 중복 없음."))
            return

        total_duplicates = sum(g["cnt"] - 1 for g in groups_list)
        self.stdout.write(
            self.style.WARNING(
                f"중복 그룹 {len(groups_list)}건, 정리 시 영구 삭제될 레코드: {total_duplicates}명"
            )
        )
        for g in groups_list[:10]:
            self.stdout.write(
                f"  tenant={g['tenant_id']} name={g['name']!r} parent_phone={g['parent_phone'][:6]}*** cnt={g['cnt']}"
            )
        if len(groups_list) > 10:
            self.stdout.write(f"  ... 외 {len(groups_list) - 10}개 그룹")

        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: 실제 삭제하지 않음. 정리하려면 --fix 를 사용하세요."))
            return

        if not do_fix:
            self.stdout.write(
                self.style.NOTICE("정리하려면 --fix 옵션을 붙여 다시 실행하세요.")
            )
            return

        removed = 0
        with transaction.atomic():
            for g in groups_list:
                keep = student_repo.student_filter_dup_keep_first(
                    g["tenant_id"], g["name"], g["parent_phone"]
                )
                to_remove = list(
                    student_repo.student_filter_dup_to_remove(
                        g["tenant_id"], g["name"], g["parent_phone"], keep.id
                    )
                )
                for s in to_remove:
                    student_repo.enrollment_filter_student_delete_obj(s)
                    user = s.user
                    s.delete()
                    if user:
                        user.delete()
                    removed += 1

        self.stdout.write(self.style.SUCCESS(f"중복 영구 삭제 완료: {removed}명"))
