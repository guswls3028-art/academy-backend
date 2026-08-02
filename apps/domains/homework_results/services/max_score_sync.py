"""Homework 만점 변경을 현재 1차 점수·판정에 일관되게 반영한다."""

from __future__ import annotations

from django.db.models import Max

from apps.domains.homework_results.models import HomeworkScore
from apps.support.homework_results.homework_view_dependencies import (
    get_homework_raw_score_cutline,
)
from apps.domains.homework_results.services.policy_recalc import (
    recalc_scores_for_homework_change,
)


_USE_EFFECTIVE_CUTLINE = object()


def validate_homework_max_score(
    *,
    homework,
    max_score: float,
    raw_score_cutline=_USE_EFFECTIVE_CUTLINE,
) -> None:
    if homework.session_id is not None:
        cutline = raw_score_cutline
        if cutline is _USE_EFFECTIVE_CUTLINE:
            cutline = get_homework_raw_score_cutline(
                session=homework.session,
                homework=homework,
            )
        if cutline is not None and float(cutline) > float(max_score):
            raise ValueError(
                f"점수 커트라인({cutline:g}점)보다 만점({max_score:g}점)을 낮게 설정할 수 없습니다."
            )

    highest_score = (
        HomeworkScore.objects
        .filter(homework=homework, attempt_index=1, score__isnull=False)
        .aggregate(value=Max("score"))
        .get("value")
    )
    if highest_score is not None and float(highest_score) > float(max_score):
        raise ValueError(
            f"현재 입력된 최고 점수({float(highest_score):g}점)보다 만점을 낮게 설정할 수 없습니다."
        )


def sync_homework_primary_score_max(*, homework, max_score: float) -> int:
    if homework.session_id is None:
        return 0
    return recalc_scores_for_homework_change(homework=homework)
