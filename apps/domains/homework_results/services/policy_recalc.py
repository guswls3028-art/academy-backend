# PATH: apps/domains/homework_results/services/policy_recalc.py
"""
HomeworkPolicy 변경 시 HomeworkScore 스냅샷(passed/clinic_required) 재계산.

분리 사유: HomeworkPolicy(homework 도메인)는 HomeworkScore(homework_results 도메인)를 직접 조작하면 안 된다.
정책 변경 → 결과 갱신은 homework_results 도메인의 책임이므로 service로 격리한다.

정책 변경으로 판정이 바뀐 모든 row의 ClinicLink를 양방향 동기화한다.
커트라인 하향 시 새 합격자의 링크를 해소하고, 상향 시 새 미달자의 링크를 만든다.
HomeworkScore만 갱신해 성적표 판정과 clinic 큐가 어긋나는 상태를 허용하지 않는다.
"""

from __future__ import annotations

from django.utils import timezone

from apps.domains.homework_results.models import HomeworkScore
from apps.support.homework_results.score_dependencies import (
    calc_homework_passed_and_clinic,
    sync_homework_clinic_link,
)


def _recalc_scores(*, queryset) -> int:
    now = timezone.now()
    changed: list[HomeworkScore] = []

    for score_snapshot in queryset.iterator(chunk_size=500):
        max_score = (
            score_snapshot.homework.default_max_score
            if score_snapshot.score is not None
            else None
        )
        passed, clinic_required, _ = calc_homework_passed_and_clinic(
            session=score_snapshot.session,
            homework=score_snapshot.homework,
            score=score_snapshot.score,
            max_score=max_score,
        )
        if (
            score_snapshot.max_score != max_score
            or score_snapshot.passed != bool(passed)
            or score_snapshot.clinic_required != bool(clinic_required)
        ):
            score_snapshot.max_score = max_score
            score_snapshot.passed = bool(passed)
            score_snapshot.clinic_required = bool(clinic_required)
            score_snapshot.updated_at = now
            changed.append(score_snapshot)

    if changed:
        HomeworkScore.objects.bulk_update(
            changed,
            fields=["max_score", "passed", "clinic_required", "updated_at"],
            batch_size=500,
        )

    for score_snapshot in changed:
        if score_snapshot.score is None:
            continue
        sync_homework_clinic_link(
            enrollment_id=int(score_snapshot.enrollment_id),
            session=score_snapshot.session,
            homework_id=int(score_snapshot.homework_id),
            passed=bool(score_snapshot.passed),
            score=score_snapshot.score,
            max_score=score_snapshot.max_score,
        )

    return len(changed)


def recalc_scores_for_policy_change(*, policy) -> int:
    """
    HomeworkPolicy 객체를 받아 attempt_index=1 HomeworkScore의 passed/clinic_required를 재계산.

    NOTE:
    - 점수 입력 시점에만 passed 계산하면, 정책(커트라인) 변경이 결과 화면에 반영되지 않는 문제가 발생한다.
    - 여기서는 score/max_score와 policy만으로 재계산 가능한 필드만 갱신한다.
    - meta.status="NOT_SUBMITTED" 등 Progress 연동은 다른 파이프라인(SSOT)이 담당한다.
    - 변경된 판정의 ClinicLink는 이 서비스가 같은 정책 변경 흐름에서 동기화한다.
    """
    queryset = HomeworkScore.objects.select_for_update().select_related(
        "homework",
        "session",
        "session__lecture",
        "session__lecture__tenant",
        "session__homework_policy",
    ).filter(
        session=policy.session,
        session__lecture__tenant=policy.tenant,
        attempt_index=1,
    )
    return _recalc_scores(queryset=queryset)


def recalc_scores_for_homework_change(*, homework) -> int:
    if homework.session_id is None:
        return 0
    queryset = HomeworkScore.objects.select_for_update().select_related(
        "homework",
        "session",
        "session__lecture",
        "session__lecture__tenant",
        "session__homework_policy",
    ).filter(
        homework=homework,
        session=homework.session,
        session__lecture__tenant=homework.tenant,
        attempt_index=1,
    )
    return _recalc_scores(queryset=queryset)
