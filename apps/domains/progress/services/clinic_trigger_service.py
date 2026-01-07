# apps/domains/progress/services/clinic_trigger_service.py
from __future__ import annotations

from apps.domains.progress.models import ClinicLink, SessionProgress

# ======================================================
# 🔧 PATCH: 시험 기반 클리닉 위험도 판단 서비스
# ======================================================
from apps.domains.progress.services.clinic_exam_rule_service import (
    ClinicExamRuleService,
)


class ClinicTriggerService:
    """
    클리닉 '필요 상태'를 생성하는 트리거 서비스

    ❗ 실제 클리닉 수업(Session)은 생성하지 않는다
    ❗ clinic 도메인과 직접 결합하지 않는다

    역할:
    - 차시(SessionProgress) 기준으로
      "이 학생은 클리닉 대상이다" 라는 사실만 기록
    """

    @staticmethod
    def auto_create_if_failed(session_progress: SessionProgress) -> None:
        """
        차시 미완료 시 자동 클리닉 대상자 생성
        (자동 트리거)
        """
        if session_progress.completed:
            return

        ClinicLink.objects.get_or_create(
            enrollment_id=session_progress.enrollment_id,
            session=session_progress.session,
            reason=ClinicLink.Reason.AUTO_FAILED,
            defaults={
                "is_auto": True,
                "approved": False,
            },
        )

    @staticmethod
    def manual_create(
        *,
        enrollment_id: int,
        session_id: int,
        reason: str,
        memo: str | None = None,
    ) -> ClinicLink:
        """
        강사/조교가 수동으로 클리닉 대상자 지정
        (합격자도 포함 가능)
        """
        return ClinicLink.objects.create(
            enrollment_id=enrollment_id,
            session_id=session_id,
            reason=reason,
            is_auto=False,
            memo=memo,
        )

    # ======================================================
    # 🔧 PATCH: 시험 결과 기반 클리닉 위험 자동 트리거
    # ======================================================
    @staticmethod
    def auto_create_if_exam_risk(
        *,
        enrollment_id: int,
        session,
        exam_id: int,
    ) -> None:
        """
        시험 결과를 기반으로 클리닉 '위험 상태' 자동 생성

        - ClinicExamRuleService를 통해 위험 사유 평가
        - 실제 점수/합불 판단 로직은 이 서비스에 존재하지 않음
        - meta.exam_reasons 에 '왜 위험한지' 근거만 기록

        ❗ 시험 합불 정책 변경 시 이 메서드는 수정 대상 아님
        """

        # 🔹 시험 위험도 평가 (단일 진실)
        reasons = ClinicExamRuleService.evaluate(
            enrollment_id=enrollment_id,
            exam_id=exam_id,
        )

        if not reasons:
            return

        ClinicLink.objects.get_or_create(
            enrollment_id=enrollment_id,
            session=session,
            # 🔹 기존 Reason 재사용 (추후 AUTO_RISK_EXAM 확장 가능)
            reason=ClinicLink.Reason.AUTO_FAILED,
            defaults={
                "is_auto": True,
                "approved": False,
                "meta": {
                    "exam_reasons": reasons,
                },
            },
        )
