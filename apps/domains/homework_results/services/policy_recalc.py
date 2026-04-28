# PATH: apps/domains/homework_results/services/policy_recalc.py
"""
HomeworkPolicy 변경 시 HomeworkScore 스냅샷(passed/clinic_required) 재계산.

분리 사유: HomeworkPolicy(homework 도메인)는 HomeworkScore(homework_results 도메인)를 직접 조작하면 안 된다.
정책 변경 → 결과 갱신은 homework_results 도메인의 책임이므로 service로 격리한다.
"""

from __future__ import annotations

from django.utils import timezone

from apps.domains.homework_results.models import HomeworkScore


def recalc_scores_for_policy_change(*, policy) -> int:
    """
    HomeworkPolicy 객체를 받아 attempt_index=1 HomeworkScore의 passed/clinic_required를 재계산.

    NOTE:
    - 점수 입력 시점에만 passed 계산하면, 정책(커트라인) 변경이 결과 화면에 반영되지 않는 문제가 발생한다.
    - 여기서는 score/max_score와 policy만으로 재계산 가능한 필드만 갱신한다.
    - meta.status="NOT_SUBMITTED" 등 Progress/ClinicLink 연동은 다른 파이프라인(SSOT)이 담당한다.
    """
    session = policy.session
    tenant = policy.tenant

    qs = HomeworkScore.objects.filter(
        session=session,
        session__lecture__tenant=tenant,
        attempt_index=1,
    ).only(
        "id",
        "score",
        "max_score",
        "passed",
        "clinic_required",
        "updated_at",
    )

    mode = str(getattr(policy, "cutline_mode", "PERCENT") or "PERCENT")
    cutline_value = int(getattr(policy, "cutline_value", 0) or 0)
    round_unit = int(getattr(policy, "round_unit_percent", 5) or 5)
    clinic_enabled = bool(getattr(policy, "clinic_enabled", True))
    clinic_on_fail = bool(getattr(policy, "clinic_on_fail", True))

    if round_unit <= 0:
        round_unit = 1

    now = timezone.now()
    changed: list[HomeworkScore] = []

    for hs in qs.iterator(chunk_size=500):
        score = hs.score
        max_score = hs.max_score

        passed = False
        clinic_required = False

        if mode == "COUNT":
            if score is not None:
                try:
                    passed = bool(float(score) >= float(cutline_value))
                except Exception:
                    passed = False
            clinic_required = bool(clinic_enabled and clinic_on_fail and (not passed))
        else:
            percent: float | None
            if score is None:
                percent = None
            elif max_score is None:
                percent = float(score)
            else:
                if float(max_score) == 0.0:
                    percent = None
                else:
                    percent = (float(score) / float(max_score)) * 100.0

            if percent is None:
                passed = False
                clinic_required = False
            else:
                rounded = int(round(float(percent) / float(round_unit)) * float(round_unit))
                threshold = int(cutline_value or 0)
                if threshold <= 0:
                    threshold = 80
                passed = bool(rounded >= threshold)
                clinic_required = bool(clinic_enabled and clinic_on_fail and (not passed))

        if hs.passed != bool(passed) or hs.clinic_required != bool(clinic_required):
            hs.passed = bool(passed)
            hs.clinic_required = bool(clinic_required)
            hs.updated_at = now
            changed.append(hs)

    if changed:
        HomeworkScore.objects.bulk_update(
            changed,
            fields=["passed", "clinic_required", "updated_at"],
            batch_size=500,
        )

    return len(changed)
