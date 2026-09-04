# apps/domains/progress/services/risk_evaluator.py
from __future__ import annotations

from apps.domains.progress.models import LectureProgress, RiskLog


class RiskEvaluator:
    """
    위험 판단 로직 (표준 SaaS 룰)

    ✅ Enterprise idempotency:
    - 동일 조건 로그는 중복 생성하지 않는다.
    """

    @staticmethod
    def _log_once(*, enrollment_id: int, session, risk_level: str, rule: str, reason: str) -> None:
        RiskLog.objects.get_or_create(
            enrollment_id=int(enrollment_id),
            session=session,
            risk_level=risk_level,
            rule=rule,
            defaults={"reason": reason},
        )

    @staticmethod
    def level_for_consecutive_failures(count: int) -> str:
        """Pure projection rule, also used when source edits must not emit logs."""
        if count >= 3:
            return LectureProgress.RiskLevel.DANGER
        if count >= 2:
            return LectureProgress.RiskLevel.WARNING
        return LectureProgress.RiskLevel.NORMAL

    @staticmethod
    def evaluate(lecture_progress: LectureProgress) -> None:
        enroll_id = int(lecture_progress.enrollment_id)
        lecture_progress.risk_level = RiskEvaluator.level_for_consecutive_failures(
            lecture_progress.consecutive_failed_sessions,
        )

        if lecture_progress.consecutive_failed_sessions >= 3:
            RiskEvaluator._log_once(
                enrollment_id=enroll_id,
                session=lecture_progress.last_session,
                risk_level=RiskLog.RiskLevel.DANGER,
                rule=RiskLog.Rule.CONSECUTIVE_INCOMPLETE,
                reason="연속 3차시 미완료",
            )

        elif lecture_progress.consecutive_failed_sessions >= 2:
            RiskEvaluator._log_once(
                enrollment_id=enroll_id,
                session=lecture_progress.last_session,
                risk_level=RiskLog.RiskLevel.WARNING,
                rule=RiskLog.Rule.CONSECUTIVE_INCOMPLETE,
                reason="연속 2차시 미완료",
            )

        lecture_progress.save(update_fields=["risk_level", "updated_at"])
