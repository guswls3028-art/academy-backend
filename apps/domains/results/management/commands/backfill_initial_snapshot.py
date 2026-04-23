"""
Legacy backfill: attempt_index=1 의 meta.initial_snapshot 을 채운다.

배경:
- "석차=1차 점수" 정책은 ExamAttempt.attempt_index=1 의 meta["initial_snapshot"]
  에 1차 점수를 불변 스냅샷으로 저장하는 것에 의존한다.
- 이 필드는 2026-04-22 이후 생성된 attempt만 자동으로 기록된다. 그 이전에 생성된
  attempt_index=1 레코드는 snapshot 이 비어 있어, ranking.py 가 Result.total_score
  로 fallback 한다.
- ONLINE 재응시가 들어오면 sync_result_from_exam_submission 이 Result.total_score
  를 2차 점수로 덮어쓰므로 석차가 "현재 Result" 값으로 오염된다.
- sync 자체에도 덮어쓰기 직전 legacy backfill이 있지만, 아직 재응시가 들어오지
  않은 attempt에 대해 미리 스냅샷을 고정하는 것이 더 안전하다(유실 범위 축소).

사용:
    python manage.py backfill_initial_snapshot [--dry-run]
                                                [--tenant <id>]
                                                [--only-at-risk]
                                                [--limit N]

옵션:
    --dry-run       변경 없이 대상만 출력.
    --tenant ID     특정 테넌트만 스캔 (Enrollment.tenant_id).
    --only-at-risk  attempt_index>=2 가 이미 존재하는 레코드만 — 이미 석차 오염이
                    발생했거나 곧 발생할 위험군.
    --limit N       한 번에 업데이트할 최대 건수 (기본: 무제한).
    --verbose       각 레코드의 복구 값을 모두 출력.

복구 품질:
    attempt_index>=2 가 없고 Result.total_score 가 아직 1차 값인 경우 → 정확 복구.
    재응시가 이미 들어와 Result.total_score 가 2차 값으로 덮어써진 경우 →
    이 command 는 "현재 Result 값"을 스냅샷에 기록하므로 2차 값이 고정된다.
    이 경우 해당 attempt 의 initial_snapshot 은 근사값이며 원래 1차 점수는 복원
    불가하다. 해당 레코드는 meta["initial_snapshot"]["source"]="legacy_backfill_cli"
    + "_warning"="possibly_overwritten" 로 명시 표시한다.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone


class Command(BaseCommand):
    help = "Backfill ExamAttempt.meta.initial_snapshot for legacy attempt_index=1 rows."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Report only; do not modify data.")
        parser.add_argument("--tenant", type=int, default=None,
                            help="Scope to a single tenant (Enrollment.tenant_id).")
        parser.add_argument("--only-at-risk", action="store_true",
                            help="Only rows where a retake (attempt_index>=2) already exists.")
        parser.add_argument("--limit", type=int, default=0,
                            help="Max rows to update (0 = unlimited).")
        parser.add_argument("--verbose", action="store_true",
                            help="Print each affected row.")

    def handle(self, *args, **options):
        from apps.domains.results.models import ExamAttempt, Result
        from apps.domains.enrollment.models import Enrollment

        dry_run = bool(options.get("dry_run"))
        tenant_id = options.get("tenant")
        only_at_risk = bool(options.get("only_at_risk"))
        limit = int(options.get("limit") or 0)
        verbose = bool(options.get("verbose"))

        base_qs = ExamAttempt.objects.filter(attempt_index=1)

        # tenant scope via Enrollment.tenant_id
        if tenant_id is not None:
            tenant_enrollment_ids = set(
                Enrollment.objects.filter(tenant_id=tenant_id).values_list("id", flat=True)
            )
            base_qs = base_qs.filter(enrollment_id__in=tenant_enrollment_ids)
            self.stdout.write(f"[scope] tenant_id={tenant_id} → {len(tenant_enrollment_ids)} enrollments")

        # "initial_snapshot 없음" 판정은 DB 벤더별 JSONB 연산자 차이를 피하기 위해
        # Python 레벨에서 수행한다 (PG/SQLite 공통 안전).
        def _needs_backfill(a) -> bool:
            m = a.meta
            return not isinstance(m, dict) or "initial_snapshot" not in m

        scanned_total = base_qs.count()
        self.stdout.write(f"[scan] attempt_index=1 rows: {scanned_total}")

        if scanned_total == 0:
            return

        # at-risk 판정용: 동일 (exam_id, enrollment_id)에 attempt_index>=2 존재 여부
        retake_rows = (
            ExamAttempt.objects
            .filter(attempt_index__gte=2)
            .values("exam_id", "enrollment_id")
            .distinct()
        )
        retake_keys: set[tuple[int, int]] = {
            (int(r["exam_id"]), int(r["enrollment_id"])) for r in retake_rows
        }
        self.stdout.write(f"[scan] distinct (exam, enrollment) with retake: {len(retake_keys)}")

        # Result map: 한 번에 prefetch (전체 attempt_index=1 기준)
        exam_ids: set[int] = set()
        enroll_ids: set[int] = set()
        for a in base_qs.only("exam_id", "enrollment_id").iterator():
            exam_ids.add(int(a.exam_id))
            enroll_ids.add(int(a.enrollment_id))
        result_qs = Result.objects.filter(
            target_type="exam",
            target_id__in=exam_ids,
            enrollment_id__in=enroll_ids,
        ).only("target_id", "enrollment_id", "total_score", "max_score", "submitted_at")
        result_map: dict[tuple[int, int], Result] = {
            (int(r.target_id), int(r.enrollment_id)): r for r in result_qs
        }

        processed = 0
        skipped_already_filled = 0
        candidate = 0
        updated = 0
        skipped_no_result = 0
        at_risk = 0

        with transaction.atomic():
            for a in base_qs.select_for_update().order_by("id").iterator(chunk_size=500):
                processed += 1
                if not _needs_backfill(a):
                    skipped_already_filled += 1
                    continue
                candidate += 1

                if limit and updated >= limit:
                    continue

                key = (int(a.exam_id), int(a.enrollment_id))
                is_at_risk = key in retake_keys

                if only_at_risk and not is_at_risk:
                    continue

                r = result_map.get(key)
                if not r:
                    skipped_no_result += 1
                    if verbose:
                        self.stdout.write(
                            f"  skip: no Result (attempt_id={a.id} exam={a.exam_id} enrollment={a.enrollment_id})"
                        )
                    continue

                snapshot = {
                    "total_score": r.total_score,
                    "max_score": r.max_score,
                    "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
                    "source": "legacy_backfill_cli",
                    "backfilled_at": timezone.now().isoformat(),
                }
                if is_at_risk:
                    snapshot["_warning"] = "possibly_overwritten_by_retake"
                    at_risk += 1

                if verbose:
                    mark = " [AT-RISK]" if is_at_risk else ""
                    self.stdout.write(
                        f"  fill: attempt_id={a.id} exam={a.exam_id} enrollment={a.enrollment_id} "
                        f"score={r.total_score}/{r.max_score}{mark}"
                    )

                if not dry_run:
                    meta = dict(a.meta or {}) if isinstance(a.meta, dict) else {}
                    # 이중 가드: 다른 경로가 동시에 채웠다면 덮어쓰지 않음.
                    if "initial_snapshot" in meta:
                        continue
                    meta["initial_snapshot"] = snapshot
                    a.meta = meta
                    a.save(update_fields=["meta", "updated_at"])
                updated += 1

            if dry_run:
                # atomic 블록 안에서 실제 save 안 했지만 안전상 롤백 의미로 raise 없이 break.
                transaction.set_rollback(True)

        self.stdout.write("")
        self.stdout.write(f"[result] processed={processed} updated={updated} "
                          f"skipped_no_result={skipped_no_result} at_risk_count={at_risk}")
        self.stdout.write(f"[result] dry_run={dry_run} tenant={tenant_id} "
                          f"only_at_risk={only_at_risk} limit={limit or 'unlimited'}")
        if dry_run:
            self.stdout.write("\n[DRY RUN] No changes committed.")

        # 추가: 덮어쓰기 추정 건은 별도로 보고
        if at_risk and not dry_run:
            self.stdout.write(
                f"\n[NOTICE] {at_risk} row(s) marked as 'possibly_overwritten_by_retake'. "
                "These are attempts whose Result may have been rewritten by a later retake "
                "BEFORE this backfill ran; the recorded snapshot is a best-effort freeze of "
                "current Result.total_score and may not be the true 1차 점수."
            )
