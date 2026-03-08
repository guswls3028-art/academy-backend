# PATH: apps/domains/community/management/commands/fix_qna_orphan_created_by.py
"""
QnA 게시물 중 created_by가 null인 것 정리.

- 원인: 학생앱에서 프로필 로드 전 제출 시 created_by가 비어 저장됨.
- 동작: block_type code='qna' 이고 created_by_id가 null인 PostEntity를 찾아,
  해당 테넌트에 활성 학생이 1명뿐이면 그 학생으로 created_by 설정.
  여러 명이면 자동 할당하지 않고 목록만 출력 (수동 지정 시 --student-id 사용).

사용:
  python manage.py fix_qna_orphan_created_by
  python manage.py fix_qna_orphan_created_by --dry-run
  python manage.py fix_qna_orphan_created_by --student-id=123  # 해당 학생으로만 할당 (테넌트 무관)
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.domains.community.models import PostEntity
from apps.domains.students.models import Student


class Command(BaseCommand):
    help = "Fix QnA posts with created_by=null (assign when tenant has single student)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="변경 없이 대상만 출력",
        )
        parser.add_argument(
            "--student-id",
            type=int,
            default=None,
            help="지정한 학생 ID로만 할당 (해당 학생이 속한 테넌트의 orphan만)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        student_id = options["student_id"]

        # QnA 블록 타입이고 created_by가 null인 글
        orphans = (
            PostEntity.objects.filter(created_by_id__isnull=True)
            .filter(block_type__code__iexact="qna")
            .select_related("tenant", "block_type")
            .order_by("tenant_id", "created_at")
        )
        if not orphans.exists():
            self.stdout.write(self.style.SUCCESS("created_by=null인 QnA 글이 없습니다."))
            return

        if student_id is not None:
            self._assign_to_student(orphans, student_id, dry_run)
            return

        # tenant별로 그룹
        by_tenant = {}
        for post in orphans:
            tid = post.tenant_id
            if tid not in by_tenant:
                by_tenant[tid] = []
            by_tenant[tid].append(post)

        updated = 0
        skipped = 0
        for tenant_id, posts in by_tenant.items():
            students = list(
                Student.objects.filter(tenant_id=tenant_id, deleted_at__isnull=True).values_list("id", flat=True)
            )
            if len(students) == 1:
                with transaction.atomic():
                    for post in posts:
                        if not dry_run:
                            post.created_by_id = students[0]
                            post.save(update_fields=["created_by_id"])
                        self.stdout.write(
                            f"  post_id={post.id} tenant_id={tenant_id} title={post.title[:40]!r} -> student_id={students[0]}"
                        )
                updated += len(posts)
            else:
                skipped += len(posts)
                self.stdout.write(
                    self.style.WARNING(
                        f"tenant_id={tenant_id}: 활성 학생 수={len(students)}명 → 자동 할당 생략 (post_ids={[p.id for p in posts]})"
                    )
                )

        if dry_run:
            self.stdout.write(self.style.NOTICE(f"[dry-run] 자동 할당 가능: {updated}건, 생략: {skipped}건"))
        else:
            self.stdout.write(self.style.SUCCESS(f"할당 완료: {updated}건, 자동 할당 생략: {skipped}건"))

    def _assign_to_student(self, orphans_queryset, student_id: int, dry_run: bool):
        student = Student.objects.filter(id=student_id, deleted_at__isnull=True).select_related("tenant").first()
        if not student:
            self.stderr.write(self.style.ERROR(f"학생 id={student_id} 없음 또는 삭제됨."))
            return
        tenant_id = student.tenant_id
        posts = list(orphans_queryset.filter(tenant_id=tenant_id))
        if not posts:
            self.stdout.write(self.style.WARNING(f"tenant_id={tenant_id}에 해당하는 orphan QnA 글이 없습니다."))
            return
        with transaction.atomic():
            for post in posts:
                if not dry_run:
                    post.created_by_id = student_id
                    post.save(update_fields=["created_by_id"])
                self.stdout.write(f"  post_id={post.id} title={post.title[:40]!r} -> student_id={student_id}")
        self.stdout.write(self.style.SUCCESS(f"{'[dry-run] ' if dry_run else ''}student_id={student_id}로 {len(posts)}건 할당."))
