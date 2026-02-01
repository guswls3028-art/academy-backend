# apps/domains/progress/services/clinic_trigger_service.py
from __future__ import annotations

from apps.domains.progress.models import ClinicLink, SessionProgress
from apps.domains.progress.services.clinic_exam_rule_service import ClinicExamRuleService


class ClinicTriggerService:
    """
    클리닉 '필요 상태'를 생성하는 트리거 서비스 (저장만)
    """

    @staticmethod
    def auto_create_if_failed(session_progress: SessionProgress) -> None:
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
        return ClinicLink.objects.create(
            enrollment_id=enrollment_id,
            session_id=session_id,
            reason=reason,
            is_auto=False,
            memo=memo,
        )

    @staticmethod
    def auto_create_if_exam_risk(
        *,
        enrollment_id: int,
        session,
        exam_id: int,
    ) -> None:
        """
        ✅ 시험 결과 기반 클리닉 위험 자동 트리거 (idempotent)

        - 동일 enroll/session에 대해 여러 번 호출되어도 get_or_create로 안정
        - meta에 exam_reasons를 누적 업데이트(덮어쓰기)
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
            # ⚠️ migration 없이 reason 확장 불가하므로 기존 값 유지
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

        # 이미 있으면 meta를 보강/갱신
        if not created:
            meta = dict(obj.meta or {})
            meta["kind"] = meta.get("kind") or "EXAM_RISK"
            meta["exam_id"] = int(exam_id)
            meta["exam_reasons"] = reasons
            obj.meta = meta
            obj.is_auto = True
            obj.save(update_fields=["meta", "is_auto", "updated_at"])
