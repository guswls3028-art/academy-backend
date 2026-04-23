"""
Legacy backfill: ClinicLink.source_type/source_id 를 채운다.

배경:
- V1.1.1 이전 생성된 ClinicLink 중 source_type 이 NULL 인 레거시 링크가 있다.
- 현재 resolve_by_exam_pass / resolve_by_homework_pass 는 엄격 매치 정책으로
  legacy 링크를 meta.exam_id / meta.homework_id 가 일치할 때만 해소한다.
  meta 에도 exam_id/homework_id 가 없으면 영구 미해소 상태로 남아 운영자가
  수동으로 처리해야 한다.
- 이 command 는 meta 안에 남아 있는 단서(exam_id, homework_id 또는 kind)를
  기반으로 source_type/source_id 를 추론해 채운다. 추론 불가 링크는 건드리지 않고
  리포트만 남긴다.

추론 규칙 (보수적, false-positive 최소화):
  1) meta["exam_id"] 존재하고 해당 Exam 이 실제로 존재
     → source_type="exam", source_id=meta["exam_id"]
  2) meta["homework_id"] 존재하고 해당 Homework 가 실제로 존재
     → source_type="homework", source_id=meta["homework_id"]
  3) (1)(2) 모두 아니지만 meta["kind"]=="EXAM_FAILED" or "EXAM_RISK"
     AND session 에 연결된 Exam 이 단 1개
     → source_type="exam", source_id=<해당 exam>
  4) 그 외 → skip (수동 조사 필요)

사용:
    python manage.py backfill_legacy_cliniclinks [--dry-run]
                                                  [--tenant <id>]
                                                  [--resolved]
                                                  [--limit N]
                                                  [--verbose]
                                                  [--report-only]

옵션:
    --dry-run      변경 없이 리포트만.
    --tenant ID    특정 테넌트만.
    --resolved     이미 해소된 legacy link 도 포함 (기본: 미해소만).
    --limit N      최대 N 건만 업데이트.
    --verbose      개별 링크 로그 출력.
    --report-only  전혀 채우지 않고 분포(총계/규칙별 예상)만 출력.
"""
from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction


class Command(BaseCommand):
    help = "Backfill ClinicLink.source_type/source_id for legacy NULL rows."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report only; no writes.")
        parser.add_argument("--tenant", type=int, default=None, help="Scope to a single tenant.")
        parser.add_argument("--resolved", action="store_true",
                            help="Include already-resolved legacy links (default: unresolved only).")
        parser.add_argument("--limit", type=int, default=0, help="Max rows to update (0=unlimited).")
        parser.add_argument("--verbose", action="store_true", help="Print each row decision.")
        parser.add_argument("--report-only", action="store_true",
                            help="Print distribution of inferable rules only. No writes.")

    def handle(self, *args, **options):
        from apps.domains.progress.models import ClinicLink
        from apps.domains.exams.models import Exam
        from apps.domains.homework_results.models import Homework

        dry_run = bool(options.get("dry_run"))
        tenant_id = options.get("tenant")
        include_resolved = bool(options.get("resolved"))
        limit = int(options.get("limit") or 0)
        verbose = bool(options.get("verbose"))
        report_only = bool(options.get("report_only"))

        qs = ClinicLink.objects.filter(source_type__isnull=True)
        if tenant_id is not None:
            qs = qs.filter(tenant_id=tenant_id)
        if not include_resolved:
            qs = qs.filter(resolved_at__isnull=True)

        total = qs.count()
        self.stdout.write(f"[scan] legacy links (source_type NULL) in scope: {total}")
        if total == 0:
            return

        # 캐시: meta 값 기반 존재 확인에 쓰일 exam/homework id 수집
        exam_ids_hint: set[int] = set()
        hw_ids_hint: set[int] = set()
        session_ids_hint: set[int] = set()
        for link in qs.only("meta", "session_id").iterator(chunk_size=500):
            m = link.meta if isinstance(link.meta, dict) else {}
            if (eid := _to_int(m.get("exam_id"))) is not None:
                exam_ids_hint.add(eid)
            if (hid := _to_int(m.get("homework_id"))) is not None:
                hw_ids_hint.add(hid)
            if link.session_id is not None:
                session_ids_hint.add(int(link.session_id))

        valid_exams = set(
            Exam.objects.filter(id__in=exam_ids_hint).values_list("id", flat=True)
        ) if exam_ids_hint else set()
        valid_homeworks = set(
            Homework.objects.filter(id__in=hw_ids_hint).values_list("id", flat=True)
        ) if hw_ids_hint else set()

        # session → 연결된 exam 매핑 (규칙 3: 단일 exam 추론용)
        session_exam_map: dict[int, list[int]] = {}
        if session_ids_hint:
            # Exam.sessions 는 M2M. 각 세션에 매달린 exam 목록을 모은다.
            # 대량일 수 있어 .values_list("sessions", "id") 로 한 번에 처리.
            for exam_id, sid in Exam.objects.filter(
                sessions__id__in=session_ids_hint
            ).values_list("id", "sessions"):
                if sid is None:
                    continue
                session_exam_map.setdefault(int(sid), []).append(int(exam_id))

        rule_counter: Counter[str] = Counter()
        skipped_reason_counter: Counter[str] = Counter()
        processed = 0
        updated = 0

        with transaction.atomic():
            for link in qs.select_for_update().order_by("id").iterator(chunk_size=500):
                processed += 1
                meta = link.meta if isinstance(link.meta, dict) else {}

                decision_type: str | None = None
                decision_id: int | None = None
                rule: str | None = None
                skip_reason: str | None = None

                # 규칙 1: meta.exam_id 존재 + 실제 Exam
                meid = _to_int(meta.get("exam_id"))
                if meid is not None:
                    if meid in valid_exams:
                        decision_type, decision_id, rule = "exam", meid, "rule1_meta_exam_id"
                    else:
                        skip_reason = "meta.exam_id_not_found"

                # 규칙 2: meta.homework_id
                if decision_type is None and skip_reason is None:
                    mhid = _to_int(meta.get("homework_id"))
                    if mhid is not None:
                        if mhid in valid_homeworks:
                            decision_type, decision_id, rule = "homework", mhid, "rule2_meta_homework_id"
                        else:
                            skip_reason = "meta.homework_id_not_found"

                # 규칙 3: kind + session 단일 exam
                if decision_type is None and skip_reason is None:
                    kind = str(meta.get("kind") or "")
                    kinds = meta.get("kinds") or []
                    exam_kind_hints = {"EXAM_FAILED", "EXAM_RISK"}
                    if (
                        link.session_id
                        and (
                            kind in exam_kind_hints
                            or any(k in exam_kind_hints for k in kinds if isinstance(k, str))
                        )
                    ):
                        candidates = session_exam_map.get(int(link.session_id), [])
                        if len(candidates) == 1:
                            decision_type, decision_id, rule = "exam", candidates[0], "rule3_session_single_exam"
                        else:
                            skip_reason = (
                                "session_multi_exam" if candidates else "session_no_exam"
                            )

                # 결정 분류
                if decision_type is None:
                    skipped_reason_counter[skip_reason or "no_inference_possible"] += 1
                    if verbose:
                        self.stdout.write(
                            f"  skip: link={link.id} tenant={link.tenant_id} "
                            f"reason={skip_reason or 'no_inference_possible'} meta={meta}"
                        )
                    continue

                rule_counter[rule or "unknown"] += 1

                if verbose:
                    self.stdout.write(
                        f"  fill: link={link.id} tenant={link.tenant_id} "
                        f"rule={rule} source={decision_type}:{decision_id}"
                    )

                if report_only:
                    continue

                if limit and updated >= limit:
                    # 추론 가능 건이지만 limit 초과 → write 스킵.
                    continue

                if not dry_run:
                    # 재진입 가드: 다른 경로가 동시에 채웠을 가능성.
                    if link.source_type is not None:
                        continue
                    link.source_type = decision_type
                    link.source_id = int(decision_id)
                    # 이력 기록: 향후 운영 조사 위해.
                    history = list(link.resolution_history or [])
                    history.append({
                        "at": _now_iso(),
                        "action": "legacy_source_backfill",
                        "rule": rule,
                        "assigned": {"source_type": decision_type, "source_id": int(decision_id)},
                    })
                    link.resolution_history = history
                    link.save(update_fields=[
                        "source_type", "source_id", "resolution_history", "updated_at",
                    ])
                updated += 1

            if dry_run or report_only:
                transaction.set_rollback(True)

        self.stdout.write("")
        self.stdout.write("[rule hits]")
        for rule, n in rule_counter.most_common():
            self.stdout.write(f"  {rule}: {n}")
        self.stdout.write("[skip reasons]")
        for reason, n in skipped_reason_counter.most_common():
            self.stdout.write(f"  {reason}: {n}")
        self.stdout.write("")
        self.stdout.write(f"[result] processed={processed} updated={updated} "
                          f"dry_run={dry_run} report_only={report_only}")


def _to_int(v) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _now_iso() -> str:
    from django.utils import timezone
    return timezone.now().isoformat()
