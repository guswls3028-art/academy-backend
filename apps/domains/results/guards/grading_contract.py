# PATH: apps/domains/results/guards/grading_contract.py
from __future__ import annotations

from typing import Any

from apps.support.results.grading_contract_dependencies import validate_exam_grading_contract


class GradingContractGuard:
    """
    Boundary guard for grading.

    목적:
    - 채점 로직 이전에 SSOT 정합성 검증
    - 런타임 import 에러 / 조용한 오답 생성 방지
    - 워커/동기 호출 공통 보호막
    """

    @staticmethod
    def validate_exam_for_grading(exam: Any) -> tuple[Any, Any]:
        return validate_exam_grading_contract(exam)
