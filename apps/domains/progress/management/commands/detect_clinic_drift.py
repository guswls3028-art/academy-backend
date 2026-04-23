"""
운영 드리프트 감지 리포트.

검사 항목:
  1) legacy ClinicLink (source_type NULL) — 자동 해소 사각지대
  2) attempt_index=1 rows without meta.initial_snapshot — 석차 오염 위험
     · at-risk: 동일 (exam, enrollment) 에 attempt_index>=2 가 이미 존재
  3) SessionProgress 집계와 개별 시험 결과 간 불일치
     (completed=True 인데 exam_passed=False 등)
  4) 해소된 ClinicLink 인데 대응 Result/Attempt 가 FAIL 상태 유지
  5) CARRIED_OVER 분포 (0010 마이그레이션 rollback 리스크 파악)

사용:
    python manage.py detect_clinic_drift            # 전체 요약
    python manage.py detect_clinic_drift --tenant 1 # 특정 테넌트
    python manage.py detect_clinic_drift --verbose  # 개별 row 샘플 출력

쓰기 작업 없음. 읽기 전용 보고서.
"""
from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Read-only drift report for exam↔clinic state."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", type=int, default=None,
                            help="Scope to a single tenant.")
        parser.add_argument("--verbose", action="store_true",
                            help="Print sample rows per category (max 20 each).")
        parser.add_argument("--sample", type=int, default=10,
                            help="Sample rows per category when verbose (default 10).")

    def handle(self, *args, **options):
        from apps.domains.progress.models import ClinicLink, SessionProgress
        from apps.domains.results.models import ExamAttempt, Result
        from apps.domains.enrollment.models import Enrollment

        tenant_id = options.get("tenant")
        verbose = bool(options.get("verbose"))
        sample = max(1, int(options.get("sample") or 10))

        self.stdout.write("=" * 60)
        self.stdout.write(
            f"Clinic drift report | tenant={tenant_id if tenant_id is not None else 'all'}"
        )
        self.stdout.write("=" * 60)

        # 공통: tenant scope
        enroll_scope: set[int] | None = None
        if tenant_id is not None:
            enroll_scope = set(
                Enrollment.objects.filter(tenant_id=tenant_id).values_list("id", flat=True)
            )
            self.stdout.write(f"[scope] enrollments in tenant: {len(enroll_scope)}")

        # ──────────────────────────────────────────
        # (1) legacy source_type NULL
        # ──────────────────────────────────────────
        legacy_qs = ClinicLink.objects.filter(source_type__isnull=True)
        if tenant_id is not None:
            legacy_qs = legacy_qs.filter(tenant_id=tenant_id)
        unresolved_legacy = legacy_qs.filter(resolved_at__isnull=True).count()
        total_legacy = legacy_qs.count()
        self.stdout.write("")
        self.stdout.write("[1] Legacy ClinicLink (source_type NULL)")
        self.stdout.write(f"    total={total_legacy}  unresolved={unresolved_legacy}")
        if verbose and total_legacy:
            for link in legacy_qs.order_by("id")[:sample]:
                self.stdout.write(
                    f"    · link={link.id} tenant={link.tenant_id} "
                    f"enroll={link.enrollment_id} session={link.session_id} "
                    f"meta={link.meta}"
                )

        # ──────────────────────────────────────────
        # (2) attempt_index=1 missing initial_snapshot
        # ──────────────────────────────────────────
        attempt1_qs = ExamAttempt.objects.filter(attempt_index=1)
        if enroll_scope is not None:
            attempt1_qs = attempt1_qs.filter(enrollment_id__in=enroll_scope)

        missing_snapshot = 0
        at_risk = 0

        # 재응시 존재 키
        retake_qs = ExamAttempt.objects.filter(attempt_index__gte=2)
        if enroll_scope is not None:
            retake_qs = retake_qs.filter(enrollment_id__in=enroll_scope)
        retake_keys = {
            (int(r["exam_id"]), int(r["enrollment_id"]))
            for r in retake_qs.values("exam_id", "enrollment_id").distinct()
        }

        at_risk_samples: list[tuple[int, int, int]] = []
        for a in attempt1_qs.only("id", "exam_id", "enrollment_id", "meta").iterator(chunk_size=500):
            meta = a.meta if isinstance(a.meta, dict) else {}
            if "initial_snapshot" in meta:
                continue
            missing_snapshot += 1
            key = (int(a.exam_id), int(a.enrollment_id))
            if key in retake_keys:
                at_risk += 1
                if len(at_risk_samples) < sample:
                    at_risk_samples.append((a.id, a.exam_id, a.enrollment_id))

        self.stdout.write("")
        self.stdout.write("[2] attempt_index=1 missing meta.initial_snapshot")
        self.stdout.write(
            f"    missing_snapshot={missing_snapshot}  at_risk_with_retake={at_risk}"
        )
        if verbose and at_risk_samples:
            self.stdout.write("    sample (at-risk rows, 석차 오염 위험):")
            for aid, eid, enid in at_risk_samples:
                self.stdout.write(
                    f"    · attempt_id={aid} exam={eid} enrollment={enid}"
                )

        # ──────────────────────────────────────────
        # (3) SessionProgress vs 개별 exam_passed 불일치
        # ──────────────────────────────────────────
        sp_qs = SessionProgress.objects.all()
        if enroll_scope is not None:
            sp_qs = sp_qs.filter(enrollment_id__in=enroll_scope)

        bad_sp = 0
        bad_sp_samples: list[tuple[int, int]] = []
        for sp in sp_qs.only("id", "completed", "exam_meta", "enrollment_id").iterator(chunk_size=500):
            if not sp.completed:
                continue
            meta = sp.exam_meta if isinstance(sp.exam_meta, dict) else {}
            exams = meta.get("exams") or []
            # completed=True 인데 시험 중 하나라도 passed=False → 드리프트 의심
            if any(not bool(e.get("passed", True)) for e in exams if isinstance(e, dict)):
                bad_sp += 1
                if len(bad_sp_samples) < sample:
                    bad_sp_samples.append((sp.id, sp.enrollment_id))

        self.stdout.write("")
        self.stdout.write("[3] SessionProgress.completed=True but some exam passed=False")
        self.stdout.write(f"    mismatched={bad_sp}")
        if verbose and bad_sp_samples:
            for spid, enid in bad_sp_samples:
                self.stdout.write(f"    · sp={spid} enrollment={enid}")

        # ──────────────────────────────────────────
        # (4) 해소된 ClinicLink 인데 1차 Result 가 FAIL 유지
        #     (정상일 수도 있다 — remediated 정책이 그것. 참고용 카운트)
        # ──────────────────────────────────────────
        resolved_exam_qs = ClinicLink.objects.filter(
            resolved_at__isnull=False,
            source_type="exam",
        )
        if tenant_id is not None:
            resolved_exam_qs = resolved_exam_qs.filter(tenant_id=tenant_id)
        resolved_by_type = Counter(
            resolved_exam_qs.values_list("resolution_type", flat=True)
        )
        self.stdout.write("")
        self.stdout.write("[4] Resolved ClinicLink (source_type=exam) by resolution_type")
        for rt, n in resolved_by_type.most_common():
            self.stdout.write(f"    {rt or '(null)'}: {n}")

        # ──────────────────────────────────────────
        # (5) CARRIED_OVER 분포 (0010 rollback 리스크)
        # ──────────────────────────────────────────
        co_qs = ClinicLink.objects.filter(resolution_type="CARRIED_OVER")
        if tenant_id is not None:
            co_qs = co_qs.filter(tenant_id=tenant_id)

        co_total = co_qs.count()
        # evidence.carried_over=True 는 구 backfill + 신규 carry_over 모두 세팅됨.
        # 구분 marker 부재는 0010 파일 주석 참조. 총계만 참고 제공.
        self.stdout.write("")
        self.stdout.write("[5] CARRIED_OVER count (0010 rollback risk surface)")
        self.stdout.write(f"    total={co_total}")

        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write("Report complete. No data was modified.")
        self.stdout.write("=" * 60)
