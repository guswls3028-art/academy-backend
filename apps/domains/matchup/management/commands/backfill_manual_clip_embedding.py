"""수동 문항 CLIP 재계산을 승인 대기 proposal 경로로 요청한다.

`--apply`도 MatchupProblem을 직접 수정하지 않는다. AI job을 enqueue하고 callback이
manual_index proposal을 만들며, 학원장 승인 후에만 운영 문항에 반영된다.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.domains.matchup.models import MatchupProblem, ProblemSegmentationProposal
from apps.support.matchup.service_dependencies import dispatch_ai_job


class Command(BaseCommand):
    help = "manual cut image embedding 재계산을 승인 대기 proposal로 enqueue."

    def add_arguments(self, parser):
        parser.add_argument(
            "--tenant-id",
            type=int,
            required=True,
            help="멀티테넌트 격리 — 한 번에 한 tenant만 처리.",
        )
        parser.add_argument("--max-rows", type=int, default=100)
        parser.add_argument("--no-cap", action="store_true")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="인덱싱 job enqueue. 원본 문항은 승인 전까지 변경하지 않음.",
        )
        parser.add_argument("--problem-id", type=int, action="append", default=[])

    def handle(self, *args, **opts):
        tenant_id = opts["tenant_id"]
        pending_problem_ids = ProblemSegmentationProposal.objects.filter(
            tenant_id=tenant_id,
            proposal_kind="manual_index",
            status__in=["pending", "needs_review"],
            target_problem_id__isnull=False,
        ).values_list("target_problem_id", flat=True)
        qs = (
            MatchupProblem.objects.filter(
                tenant_id=tenant_id,
                meta__manual=True,
            )
            .exclude(image_key="")
            .exclude(id__in=pending_problem_ids)
            .order_by("id")
        )
        if opts["problem_id"]:
            qs = qs.filter(id__in=opts["problem_id"])
        if not opts["no_cap"]:
            qs = qs[: opts["max_rows"]]

        problems = list(qs)
        self.stdout.write(
            f"[manual index proposal] tenant={tenant_id} eligible={len(problems)} apply={bool(opts['apply'])}"
        )
        if not opts["apply"]:
            for problem in problems[:10]:
                self.stdout.write(
                    f"  problem={problem.id} doc={problem.document_id} image_key={problem.image_key}"
                )
            if len(problems) > 10:
                self.stdout.write(f"  ...+{len(problems) - 10} more")
            return

        enqueued = 0
        failed = 0
        for problem in problems:
            result = dispatch_ai_job(
                job_type="matchup_manual_index",
                payload={
                    "problem_id": problem.id,
                    "tenant_id": str(problem.tenant_id),
                    "image_key": problem.image_key,
                    "is_camera_capture": bool((problem.meta or {}).get("paste")),
                },
                tenant_id=str(problem.tenant_id),
                source_domain="matchup_manual",
                source_id=str(problem.id),
            )
            if isinstance(result, dict) and result.get("ok", True):
                enqueued += 1
                self.stdout.write(
                    f"  [enqueued] problem={problem.id} job={result.get('job_id', '')}"
                )
            else:
                failed += 1
                error = result.get("error") if isinstance(result, dict) else "invalid dispatch response"
                self.stderr.write(
                    f"  [failed] problem={problem.id} error={error}"
                )

        self.stdout.write(f"완료. enqueued={enqueued}, failed={failed}")
