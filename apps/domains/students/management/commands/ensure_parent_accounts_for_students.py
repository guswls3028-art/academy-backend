# PATH: apps/domains/students/management/commands/ensure_parent_accounts_for_students.py
"""
등록된 모든 학생에 대해 학부모 전화번호로 학부모 계정 생성 (일회성 마이그레이션).

- deleted_at 이 없는(활성) 학생 중 parent_phone 이 있는 경우만 처리
- 이미 해당 전화번호로 학부모 계정이 있으면 User만 없을 때 생성 후 비밀번호 동기화
- 학부모 비밀번호: 해당 학생의 현재 비밀번호(해시 복사)로 맞춤 → 학부모는 전화번호 + 학생과 동일 비밀번호로 로그인 가능

사용:
  python manage.py ensure_parent_accounts_for_students              # 실행
  python manage.py ensure_parent_accounts_for_students --dry-run    # 대상만 출력
"""
import secrets

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.domains.students.models import Student
from apps.domains.parents.services import ensure_parent_for_student
from academy.adapters.db.django import repositories_core as core_repo


def _normalize_phone(raw: str) -> str:
    return (raw or "").strip().replace("-", "").replace(" ", "").replace(".", "")


class Command(BaseCommand):
    help = "등록된 모든 학생에 대해 학부모 전화번호로 학부모 계정 생성 (마이그레이션)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="대상만 출력, 실제 생성/수정 없음",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # 활성 학생 중 학부모 전화번호가 있는 것만
        students = list(
            Student.objects.filter(deleted_at__isnull=True)
            .exclude(parent_phone__isnull=True)
            .exclude(parent_phone="")
            .select_related("tenant", "user")
            .order_by("tenant_id", "id")
        )

        if not students:
            self.stdout.write(self.style.SUCCESS("대상 학생 없음 (활성 학생 중 parent_phone 있는 경우만)."))
            return

        self.stdout.write(f"대상 학생 수: {len(students)}명 (tenant별·id순)")
        for s in students[:10]:
            phone = _normalize_phone(s.parent_phone)
            self.stdout.write(f"  tenant={s.tenant_id} student_id={s.id} name={s.name!r} parent_phone={phone[:6]}***")
        if len(students) > 10:
            self.stdout.write(f"  ... 외 {len(students) - 10}명")

        if dry_run:
            self.stdout.write(self.style.WARNING("--dry-run: 실제 생성/수정하지 않음."))
            return

        created_users = 0
        synced_passwords = 0
        errors = []

        with transaction.atomic():
            for student in students:
                try:
                    parent_phone = _normalize_phone(student.parent_phone)
                    if not parent_phone or len(parent_phone) < 8:
                        continue
                    had_user_before = False
                    existing_parent = core_repo.parent_get_by_tenant_phone(student.tenant, parent_phone)
                    if existing_parent and existing_parent.user_id:
                        had_user_before = True

                    # 학부모 계정 생성(없으면) — 임시 비밀번호로 생성
                    parent = ensure_parent_for_student(
                        tenant=student.tenant,
                        parent_phone=parent_phone,
                        student_name=student.name or "학생",
                        parent_password=secrets.token_urlsafe(16),
                    )
                    if not parent.user_id:
                        errors.append(f"student_id={student.id}: parent user 생성 실패")
                        continue
                    if not had_user_before:
                        created_users += 1

                    # 학부모 비밀번호를 해당 학생 비밀번호(해시)와 동일하게 맞춤
                    if student.user_id and parent.user_id and parent.user_id != student.user_id:
                        parent.user.password = student.user.password
                        parent.user.save(update_fields=["password"])
                        synced_passwords += 1
                except Exception as e:
                    errors.append(f"student_id={student.id} ({getattr(student, 'name', '')}): {e}")

        if errors:
            for msg in errors[:20]:
                self.stdout.write(self.style.ERROR(msg))
            if len(errors) > 20:
                self.stdout.write(self.style.ERROR(f"... 외 {len(errors) - 20}건 오류"))
        self.stdout.write(
            self.style.SUCCESS(
                f"완료: 신규 학부모 User 생성={created_users}, 비밀번호 동기화={synced_passwords}, 오류={len(errors)}건"
            )
        )
