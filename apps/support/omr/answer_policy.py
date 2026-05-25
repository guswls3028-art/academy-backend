from __future__ import annotations

from typing import Any

from apps.domains.results.services.answer_matching import answer_matches, correct_answer_sets


def answer_matches_expected(student_answer: Any, correct_answer: Any) -> bool:
    return answer_matches(student_answer, correct_answer)


def ambiguous_answer_can_change_score(
    *,
    detected_values: list[str],
    correct_answer: Any,
) -> bool:
    """
    애매한 마킹이 실제 점수를 바꿀 가능성이 있을 때만 검토로 보낸다.
    정답 후보와 전혀 겹치지 않는 애매한 선/낙서는 자동 오답으로 확정 가능하다.
    """
    detected = frozenset(str(v).strip() for v in detected_values if str(v).strip())
    if not detected:
        return False

    correct_sets = correct_answer_sets(correct_answer)
    if not correct_sets:
        return True
    if detected in correct_sets:
        return False

    return any(bool(detected & correct) for correct in correct_sets)
