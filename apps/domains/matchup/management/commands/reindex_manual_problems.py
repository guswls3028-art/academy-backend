# PATH: apps/domains/matchup/management/commands/reindex_manual_problems.py
# 임베딩이 비어 있는 매뉴얼 크롭 problem들에 OCR + 임베딩 잡 일괄 enqueue.
"""
사용:
  python manage.py reindex_manual_problems --tenant-id 1 --doc-id 152
  python manage.py reindex_manual_problems --tenant-id 1 --doc-id 152 --apply

대상: meta.manual=True 이고 embedding=NULL 또는 비어있고 image_key 있는 problem.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.domains.matchup.models import MatchupProblem, ProblemSegmentationProposal
from apps.support.matchup.service_dependencies import dispatch_ai_job


class Command(BaseCommand):
    help = "수동 크롭 problem 중 임베딩이 비어 있는 항목에 인덱싱 잡을 일괄 enqueue."

    def add_arguments(self, parser):
        parser.add_argument("--tenant-id", type=int, required=True)
        parser.add_argument("--doc-id", type=int, default=None)
        parser.add_argument(
            "--apply",
            action="store_true",
            help="AI job을 enqueue한다. 원본 문항은 별도 승인 전까지 변경되지 않는다.",
        )
        parser.add_argument("--limit", type=int, default=500)

    def handle(self, *args, **opts):
        tenant_id = opts["tenant_id"]
        pending_problem_ids = ProblemSegmentationProposal.objects.filter(
            tenant_id=tenant_id,
            proposal_kind="manual_index",
            status__in=["pending", "needs_review"],
            target_problem_id__isnull=False,
        ).values_list("target_problem_id", flat=True)
        qs = MatchupProblem.objects.filter(
            tenant_id=tenant_id,
            embedding__isnull=True,
            meta__manual=True,
        ).exclude(image_key="").exclude(id__in=pending_problem_ids)

        if opts.get("doc_id"):
            qs = qs.filter(document_id=opts["doc_id"])

        qs = qs.order_by("id")[: opts["limit"]]
        problems = list(qs)
        self.stdout.write(f"대상 problems: {len(problems)}건")

        if not opts["apply"]:
            for p in problems:
                self.stdout.write(
                    f"  problem={p.id} doc={p.document_id} num={p.number} image_key={p.image_key}"
                )
            self.stdout.write("dry-run: --apply를 지정해야 job을 enqueue합니다.")
            return

        enqueued = 0
        failed = 0
        for p in problems:
            try:
                result = dispatch_ai_job(
                    job_type="matchup_manual_index",
                    payload={
                        "problem_id": p.id,
                        "tenant_id": str(p.tenant_id),
                        "image_key": p.image_key,
                    },
                    tenant_id=str(p.tenant_id),
                    source_domain="matchup_manual",
                    source_id=str(p.id),
                )
                ok = isinstance(result, dict) and result.get("ok", True)
                if ok:
                    enqueued += 1
                    self.stdout.write(
                        f"  [enqueued] problem={p.id} job={result.get('job_id', '')}"
                    )
                else:
                    failed += 1
                    self.stdout.write(
                        f"  [FAIL] problem={p.id} error={result.get('error')}"
                    )
            except Exception as e:
                failed += 1
                self.stderr.write(f"  [EXC] problem={p.id} {e}")

        self.stdout.write(
            f"\n완료. enqueued={enqueued}, failed={failed}"
        )
