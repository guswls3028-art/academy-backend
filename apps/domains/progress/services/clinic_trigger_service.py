# apps/domains/progress/services/clinic_trigger_service.py
"""
V1.1.2: 시험/과제별 개별 ClinicLink 생성

핵심 변경:
- auto_create_if_failed: SessionProgress.completed 대신 exam_meta의 개별 시험 pass/fail로 판정
- auto_create_if_exam_risk: source_type/source_id로 시험별 개별 ClinicLink 생성
- 세션 MAX 집계와 독립적으로 개별 시험 불합격을 추적
"""
from __future__ import annotations

import logging

from apps.domains.progress.models import ClinicLink, SessionProgress
from apps.domains.progress.services.clinic_exam_rule_service import ClinicExamRuleService

logger = logging.getLogger(__name__)


class ClinicTriggerService:
    """
    클리닉 '필요 상태'를 생성하는 트리거 서비스 (저장만)
    """

    @staticmethod
    def auto_create_per_exam(session_progress: SessionProgress) -> None:
        """
        V1.1.2: 개별 시험 단위로 ClinicLink 생성.
        세션 집계(completed)와 독립적으로, 각 시험의 pass/fail을 개별 판정.
        """
        exam_meta = session_progress.exam_meta or {}
        exam_rows = exam_meta.get("exams", [])

        for exam_row in exam_rows:
            exam_id = int(exam_row.get("exam_id", 0) or 0)
            if not exam_id:
                continue

            passed = exam_row.get("passed", True)
            if passed:
                continue  # 이 시험은 합격 → ClinicLink 불필요

            # 불합격 시험에 대해 개별 ClinicLink 생성
            ClinicLink.objects.get_or_create(
                enrollment_id=session_progress.enrollment_id,
                session=session_progress.session,
                source_type="exam",
                source_id=exam_id,
                reason=ClinicLink.Reason.AUTO_FAILED,
                defaults={
                    "is_auto": True,
                    "approved": False,
                    "meta": {
                        "kind": "EXAM_FAILED",
                        "exam_id": exam_id,
                        "score": exam_row.get("score"),
                        "pass_score": exam_row.get("pass_score"),
                    },
                },
            )

    @staticmethod
    def auto_create_if_failed(session_progress: SessionProgress) -> None:
        """
        V1.1.2: 개별 시험 단위 ClinicLink로 전환.
        SessionProgress.completed를 보지 않고, exam_meta의 개별 pass/fail로 판정.
        """
        # 개별 시험 단위 생성으로 위임
        ClinicTriggerService.auto_create_per_exam(session_progress)

    @staticmethod
    def manual_create(
        *,
        enrollment_id: int,
        session_id: int,
        reason: str,
        memo: str | None = None,
        source_type: str | None = None,
        source_id: int | None = None,
    ) -> ClinicLink:
        return ClinicLink.objects.create(
            enrollment_id=enrollment_id,
            session_id=session_id,
            reason=reason,
            is_auto=False,
            memo=memo,
            source_type=source_type,
            source_id=source_id,
        )

    @staticmethod
    def auto_create_if_exam_risk(
        *,
        enrollment_id: int,
        session,
        exam_id: int,
    ) -> None:
        """
        V1.1.2: source_type/source_id로 시험별 개별 ClinicLink 생성.
        """
        reasons = ClinicExamRuleService.evaluate(
            enrollment_id=int(enrollment_id),
            exam_id=int(exam_id),
        )
        if not reasons:
            return

        obj, created = ClinicLink.objects.get_or_create(
            enrollment_id=int(enrollment_id),
            session=session,
            source_type="exam",
            source_id=int(exam_id),
            reason=ClinicLink.Reason.AUTO_FAILED,
            defaults={
                "is_auto": True,
                "approved": False,
                "meta": {
                    "kind": "EXAM_RISK",
                    "exam_id": int(exam_id),
                    "exam_reasons": reasons,
                },
            },
        )

        # 이미 있으면 meta 보강 (exam_reasons 갱신)
        if not created:
            meta = dict(obj.meta or {})
            meta["kind"] = meta.get("kind") or "EXAM_RISK"
            meta["exam_id"] = int(exam_id)
            meta["exam_reasons"] = reasons
            obj.meta = meta
            obj.is_auto = True
            obj.save(update_fields=["meta", "is_auto", "updated_at"])
