# PATH: apps/domains/matchup/management/commands/reanalyze_matchup_doc.py
"""
매치업 문서 재분석 — 기존 problems 삭제 + AI 재처리.

Status가 done이라도 재처리. UI의 "재시도"는 failed에서만 작동하므로
번호 misalign 같은 사후 발견 이슈에 대응하기 위해 별도 명령으로 분리.

Usage:
  python manage.py reanalyze_matchup_doc --doc-id 61
  python manage.py reanalyze_matchup_doc --tenant-id 1 --all-done
  python manage.py reanalyze_matchup_doc --tenant-id 1 --doc-ids 61 60 59
"""
from django.core.management.base import BaseCommand, CommandError

from apps.domains.matchup.models import MatchupDocument
from apps.domains.matchup.services import retry_document


class Command(BaseCommand):
    help = "매치업 문서를 재분석 (status 무관, 기존 문제 삭제 후 AI 재실행)"

    def add_arguments(self, parser):
        parser.add_argument("--doc-id", type=int, default=None)
        parser.add_argument("--doc-ids", nargs="+", type=int, default=None)
        parser.add_argument("--tenant-id", type=int, default=None)
        parser.add_argument(
            "--all-done", action="store_true",
            help="대상 테넌트의 done 상태 문서 전부 재처리",
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        doc_id = options["doc_id"]
        doc_ids = options["doc_ids"]
        tenant_id = options["tenant_id"]
        all_done = options["all_done"]
        dry_run = options["dry_run"]

        qs = MatchupDocument.objects.all()
        if tenant_id is not None:
            qs = qs.filter(tenant_id=tenant_id)

        targets: list[MatchupDocument]
        if doc_id is not None:
            targets = list(qs.filter(id=doc_id))
        elif doc_ids:
            targets = list(qs.filter(id__in=doc_ids))
        elif all_done:
            if tenant_id is None:
                raise CommandError("--all-done은 --tenant-id와 함께 사용")
            targets = list(qs.filter(status="done"))
        else:
            raise CommandError("--doc-id / --doc-ids / --all-done 중 하나 필요")

        if not targets:
            self.stdout.write(self.style.WARNING("대상 없음"))
            return

        self.stdout.write(f"대상: {len(targets)}개")
        for d in targets:
            self.stdout.write(
                f"  id={d.id} tenant={d.tenant_id} status={d.status} "
                f"count={d.problem_count} title={d.title[:40]}"
            )

        if dry_run:
            self.stdout.write(self.style.NOTICE("dry-run — 실제 dispatch 안 함"))
            return

        for d in targets:
            try:
                job_id = retry_document(d)
                self.stdout.write(self.style.SUCCESS(
                    f"OK doc={d.id} new_job={job_id}"
                ))
            except Exception as e:
                self.stdout.write(self.style.ERROR(
                    f"FAIL doc={d.id} error={e}"
                ))
