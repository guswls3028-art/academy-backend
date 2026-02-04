# PATH: apps/domains/homework_results/utils/score_status.py

from __future__ import annotations

from typing import Any, Literal, Optional, Tuple

from apps.domains.homework_results.models.score import HomeworkScore

HomeworkScoreState = Literal["UNSET", "NOT_SUBMITTED", "ZERO", "SCORED"]


def _meta_status(meta: Any) -> Optional[str]:
    if not isinstance(meta, dict):
        return None
    v = meta.get("status")
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def classify_homework_score_state(
    *,
    score: Optional[float],
    meta: Any,
) -> Tuple[HomeworkScoreState, Optional[str]]:
    """
    ✅ 상태 판별 단일 함수 (if-else 고정 / 서버 내부 SSOT)

    - UNSET        : score=None & meta.status=None
    - NOT_SUBMITTED: meta.status == "NOT_SUBMITTED"
    - ZERO         : score == 0
    - SCORED       : score > 0 (또는 일반적인 점수 입력 완료)

    반환:
      (state, meta_status)
    """
    st = _meta_status(meta)

    if st == HomeworkScore.MetaStatus.NOT_SUBMITTED:
        return "NOT_SUBMITTED", st

    if score is None and st is None:
        return "UNSET", st

    # NOTE: score가 0.0이면 "가져왔으나 안 함(0점)"으로 취급
    if score is not None and float(score) == 0.0:
        return "ZERO", st

    return "SCORED", st
