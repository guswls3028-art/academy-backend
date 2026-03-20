# apps/domains/progress/services/clinic_resolution_service.py
"""
클리닉 해소 서비스 (SSOT)

해소 트리거:
1. 시험 재시험 통과 (EXAM_PASS) — auto
2. 과제 재제출/재채점 통과 (HOMEWORK_PASS) — auto
3. 관리자 수동 해소 (MANUAL_OVERRIDE)
4. 면제 (WAIVED)

절대 금지:
- 예약(booking)으로 해소
- 출석(attended)으로 해소
"""
from __future__ import annotations

import logging
from typing import Optional

from django.db import transaction
from django.utils import timezone

from apps.domains.progress.models import ClinicLink

logger = logging.getLogger(__name__)


class ClinicResolutionService:
    """
    ClinicLink 해소 단일 진실 서비스.
    모든 해소/복원은 이 서비스를 통해야 한다.
    """

    @staticmethod
    @transaction.atomic
    def resolve_by_exam_pass(
        *,
        enrollment_id: int,
        session_id: int,
        exam_id: int,
        attempt_id: Optional[int] = None,
        score: Optional[float] = None,
        pass_score: Optional[float] = None,
    ) -> int:
        """
        시험 통과 시 해당 enrollment+session의 미해소 ClinicLink를 해소.
        Returns: 해소된 ClinicLink 수
        """
        evidence = {
            "exam_id": exam_id,
            "score": score,
            "pass_score": pass_score,
        }
        if attempt_id:
            evidence["attempt_id"] = attempt_id

        now = timezone.now()
        update_kwargs = dict(
            resolved_at=now,
            resolution_type=ClinicLink.ResolutionType.EXAM_PASS,
            resolution_evidence=evidence,
        )

        # V1.1.2: source-specific 해소 (시험별 개별)
        count = ClinicLink.objects.filter(
            enrollment_id=enrollment_id,
            session_id=session_id,
            source_type="exam",
            source_id=int(exam_id),
            resolved_at__isnull=True,
        ).update(**update_kwargs)

        # Fallback: legacy links (source_type=NULL, V1.1.1 이전 데이터)
        if count == 0:
            count = ClinicLink.objects.filter(
                enrollment_id=enrollment_id,
                session_id=session_id,
                source_type__isnull=True,
                resolved_at__isnull=True,
            ).update(**update_kwargs)

        if count > 0:
            logger.info(
                "clinic_resolution: EXAM_PASS resolved %d links "
                "(enrollment=%s, session=%s, exam=%s, score=%s)",
                count, enrollment_id, session_id, exam_id, score,
            )
        return count

    @staticmethod
    @transaction.atomic
    def resolve_by_homework_pass(
        *,
        enrollment_id: int,
        session_id: int,
        homework_id: int,
        score: Optional[float] = None,
        max_score: Optional[float] = None,
    ) -> int:
        """
        과제 통과 시 해당 enrollment+session의 미해소 ClinicLink를 해소.
        Returns: 해소된 ClinicLink 수
        """
        evidence = {
            "homework_id": homework_id,
            "score": score,
            "max_score": max_score,
        }

        now = timezone.now()
        count = ClinicLink.objects.filter(
            enrollment_id=enrollment_id,
            session_id=session_id,
            resolved_at__isnull=True,
        ).update(
            resolved_at=now,
            resolution_type=ClinicLink.ResolutionType.HOMEWORK_PASS,
            resolution_evidence=evidence,
        )

        if count > 0:
            logger.info(
                "clinic_resolution: HOMEWORK_PASS resolved %d links "
                "(enrollment=%s, session=%s, homework=%s)",
                count, enrollment_id, session_id, homework_id,
            )
        return count

    @staticmethod
    @transaction.atomic
    def resolve_manually(
        *,
        clinic_link_id: int,
        user_id: Optional[int] = None,
        memo: Optional[str] = None,
    ) -> Optional[ClinicLink]:
        """
        관리자 수동 해소.
        """
        try:
            link = ClinicLink.objects.select_for_update().get(
                id=clinic_link_id,
                resolved_at__isnull=True,
            )
        except ClinicLink.DoesNotExist:
            logger.warning("clinic_resolution: manual resolve failed, link not found or already resolved (id=%s)", clinic_link_id)
            return None

        link.resolved_at = timezone.now()
        link.resolution_type = ClinicLink.ResolutionType.MANUAL_OVERRIDE
        link.resolution_evidence = {"user_id": user_id, "memo": memo}
        if memo:
            link.memo = memo
        link.save(update_fields=["resolved_at", "resolution_type", "resolution_evidence", "memo", "updated_at"])

        logger.info("clinic_resolution: MANUAL_OVERRIDE (link=%s, user=%s)", clinic_link_id, user_id)
        return link

    @staticmethod
    @transaction.atomic
    def waive(
        *,
        clinic_link_id: int,
        user_id: Optional[int] = None,
        memo: Optional[str] = None,
    ) -> Optional[ClinicLink]:
        """
        면제 처리.
        """
        try:
            link = ClinicLink.objects.select_for_update().get(
                id=clinic_link_id,
                resolved_at__isnull=True,
            )
        except ClinicLink.DoesNotExist:
            logger.warning("clinic_resolution: waive failed, link not found or already resolved (id=%s)", clinic_link_id)
            return None

        link.resolved_at = timezone.now()
        link.resolution_type = ClinicLink.ResolutionType.WAIVED
        link.resolution_evidence = {"user_id": user_id, "memo": memo}
        if memo:
            link.memo = memo
        link.save(update_fields=["resolved_at", "resolution_type", "resolution_evidence", "memo", "updated_at"])

        logger.info("clinic_resolution: WAIVED (link=%s, user=%s)", clinic_link_id, user_id)
        return link

    @staticmethod
    @transaction.atomic
    def unresolve(
        *,
        clinic_link_id: int,
    ) -> Optional[ClinicLink]:
        """
        해소 취소 (되돌리기). 재시험 실패 시 등에 사용.
        """
        try:
            link = ClinicLink.objects.select_for_update().get(id=clinic_link_id)
        except ClinicLink.DoesNotExist:
            return None

        if not link.resolved_at:
            return link  # already unresolved

        link.resolved_at = None
        link.resolution_type = None
        link.resolution_evidence = None
        link.save(update_fields=["resolved_at", "resolution_type", "resolution_evidence", "updated_at"])

        logger.info("clinic_resolution: UNRESOLVED (link=%s)", clinic_link_id)
        return link

    @staticmethod
    @transaction.atomic
    def carry_over(
        *,
        clinic_link_id: int,
    ) -> Optional[ClinicLink]:
        """
        미해소 case를 다음 cycle로 이월.
        현재 link를 WAIVED(이월)로 닫고, 새 link를 cycle_no+1로 생성.
        """
        try:
            link = ClinicLink.objects.select_for_update().get(
                id=clinic_link_id,
                resolved_at__isnull=True,
            )
        except ClinicLink.DoesNotExist:
            logger.warning("clinic_resolution: carry_over failed, link not found (id=%s)", clinic_link_id)
            return None

        # Close current with carry_over marker
        now = timezone.now()
        link.resolved_at = now
        link.resolution_type = ClinicLink.ResolutionType.WAIVED
        link.resolution_evidence = {"carried_over": True, "carried_at": now.isoformat()}
        link.save(update_fields=["resolved_at", "resolution_type", "resolution_evidence", "updated_at"])

        # Create next cycle (source 전파)
        new_link = ClinicLink.objects.create(
            enrollment_id=link.enrollment_id,
            session_id=link.session_id,
            source_type=link.source_type,
            source_id=link.source_id,
            reason=link.reason,
            is_auto=link.is_auto,
            approved=False,
            cycle_no=link.cycle_no + 1,
            memo=f"{link.cycle_no}차 이월",
            meta=link.meta,
        )

        logger.info(
            "clinic_resolution: CARRIED_OVER (old=%s→new=%s, cycle=%d→%d)",
            clinic_link_id, new_link.id, link.cycle_no, new_link.cycle_no,
        )
        return new_link
