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


def _append_history(link, *, action: str, at=None) -> None:
    """
    ClinicLink.resolution_history에 현재 상태 snapshot을 append.
    transition 직전에 호출. 저장은 호출자 책임 (update_fields에 resolution_history 포함).
    """
    at = at or timezone.now()
    history = list(link.resolution_history or [])
    history.append({
        "at": at.isoformat(),
        "action": action,
        "prev_resolution_type": link.resolution_type,
        "prev_resolved_at": link.resolved_at.isoformat() if link.resolved_at else None,
        "prev_evidence": link.resolution_evidence,
    })
    link.resolution_history = history


def _send_resolution_notification(enrollment_id: int, session_id: int, resolution_type: str):
    """클리닉 해소 완료 알림 (best-effort, on_commit에서 호출)."""
    try:
        from apps.domains.enrollment.models import Enrollment
        from apps.support.messaging.services import send_event_notification

        enr = Enrollment.objects.select_related("student", "tenant", "lecture").filter(
            id=enrollment_id,
        ).first()
        if not enr or not enr.student or not enr.tenant:
            return

        # 해소 결과 라벨 매핑
        result_label = {
            "EXAM_PASS": "시험 통과",
            "HOMEWORK_PASS": "과제 통과",
            "MANUAL_OVERRIDE": "수동 해소",
            "WAIVED": "면제",
        }.get(resolution_type, "해소")

        # 세션 정보 (장소/날짜/시간) — 통합 알림톡 템플릿에 필요
        session_location = ""
        session_date = ""
        session_time = ""
        if session_id:
            try:
                from apps.domains.clinic.models import Session as ClinicSession
                cs = ClinicSession.objects.filter(pk=session_id).first()
                if cs:
                    session_location = getattr(cs, "location", "") or ""
                    session_date = str(cs.date) if cs.date else ""
                    session_time = str(cs.start_time)[:5] if getattr(cs, "start_time", None) else ""
            except Exception:
                pass

        context = {
            "클리닉명": str(getattr(enr.lecture, "title", "") or ""),
            "클리닉합불": result_label,
            "장소": session_location,
            "날짜": session_date,
            "시간": session_time,
            "_domain_object_id": f"{enrollment_id}:{session_id}",
        }
        for send_to in ("parent", "student"):
            send_event_notification(
                tenant=enr.tenant,
                trigger="clinic_result_notification",
                student=enr.student,
                send_to=send_to,
                context=context,
            )
    except Exception:
        logger.debug("clinic_result_notification failed (enrollment=%s)", enrollment_id, exc_info=True)


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

        # V1.1.2: source-specific 해소 (시험별 개별)
        # history 보존 위해 row 순회 (자동 해소는 보통 1~2건)
        target_links = list(
            ClinicLink.objects.select_for_update().filter(
                enrollment_id=enrollment_id,
                session_id=session_id,
                source_type="exam",
                source_id=int(exam_id),
                resolved_at__isnull=True,
            )
        )
        count = 0
        for link in target_links:
            _append_history(link, action="resolve_exam_pass", at=now)
            link.resolved_at = now
            link.resolution_type = ClinicLink.ResolutionType.EXAM_PASS
            link.resolution_evidence = evidence
            link.save(update_fields=[
                "resolved_at", "resolution_type", "resolution_evidence",
                "resolution_history", "updated_at",
            ])
            count += 1

        # update_kwargs는 legacy fallback에서 재사용 (기존 의미 유지)
        update_kwargs = dict(
            resolved_at=now,
            resolution_type=ClinicLink.ResolutionType.EXAM_PASS,
            resolution_evidence=evidence,
        )

        # Fallback: legacy links (source_type=NULL, V1.1.1 이전 데이터)
        # 엄격 매치: meta.exam_id가 현재 exam_id와 일치하는 링크만 해소.
        # exam_id 미상인 legacy는 자동 해소 대상에서 제외하여 과매칭 방지.
        if count == 0:
            legacy_link = ClinicLink.objects.filter(
                enrollment_id=enrollment_id,
                session_id=session_id,
                source_type__isnull=True,
                resolved_at__isnull=True,
                meta__exam_id=int(exam_id),
            ).order_by("id").first()
            if legacy_link:
                _append_history(legacy_link, action="resolve_exam_pass_legacy", at=now)
                for k, v in update_kwargs.items():
                    setattr(legacy_link, k, v)
                legacy_link.save(update_fields=list(update_kwargs.keys()) + [
                    "resolution_history", "updated_at",
                ])
                count = 1
            else:
                # meta.exam_id 매칭 실패 — 불명확한 legacy 링크는 건드리지 않음
                ambiguous = ClinicLink.objects.filter(
                    enrollment_id=enrollment_id,
                    session_id=session_id,
                    source_type__isnull=True,
                    resolved_at__isnull=True,
                ).count()
                if ambiguous:
                    logger.warning(
                        "clinic_resolution: EXAM_PASS skipped %d legacy link(s) "
                        "without matching meta.exam_id=%s (enrollment=%s, session=%s) — "
                        "manual backfill recommended",
                        ambiguous, exam_id, enrollment_id, session_id,
                    )

        if count > 0:
            logger.info(
                "clinic_resolution: EXAM_PASS resolved %d links "
                "(enrollment=%s, session=%s, exam=%s, score=%s)",
                count, enrollment_id, session_id, exam_id, score,
            )
            _eid, _sid = enrollment_id, session_id
            transaction.on_commit(lambda: _send_resolution_notification(_eid, _sid, "EXAM_PASS"))
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
        과제 통과 시 해당 enrollment+session+homework의 미해소 ClinicLink를 해소.
        Returns: 해소된 ClinicLink 수
        """
        evidence = {
            "homework_id": homework_id,
            "score": score,
            "max_score": max_score,
        }

        now = timezone.now()

        # V1.1.2: source-specific 해소 (과제별 개별) — exam과 동일 패턴
        target_links = list(
            ClinicLink.objects.select_for_update().filter(
                enrollment_id=enrollment_id,
                session_id=session_id,
                source_type="homework",
                source_id=int(homework_id),
                resolved_at__isnull=True,
            )
        )
        count = 0
        for link in target_links:
            _append_history(link, action="resolve_homework_pass", at=now)
            link.resolved_at = now
            link.resolution_type = ClinicLink.ResolutionType.HOMEWORK_PASS
            link.resolution_evidence = evidence
            link.save(update_fields=[
                "resolved_at", "resolution_type", "resolution_evidence",
                "resolution_history", "updated_at",
            ])
            count += 1

        update_kwargs = dict(
            resolved_at=now,
            resolution_type=ClinicLink.ResolutionType.HOMEWORK_PASS,
            resolution_evidence=evidence,
        )

        # Fallback: legacy links (source_type=NULL, V1.1.1 이전 데이터)
        # 엄격 매치: meta.homework_id 일치하는 링크만 해소. 과매칭 방지.
        if count == 0:
            legacy_link = ClinicLink.objects.filter(
                enrollment_id=enrollment_id,
                session_id=session_id,
                source_type__isnull=True,
                resolved_at__isnull=True,
                meta__homework_id=int(homework_id),
            ).order_by("id").first()
            if legacy_link:
                _append_history(legacy_link, action="resolve_homework_pass_legacy", at=now)
                for k, v in update_kwargs.items():
                    setattr(legacy_link, k, v)
                legacy_link.save(update_fields=list(update_kwargs.keys()) + [
                    "resolution_history", "updated_at",
                ])
                count = 1
            else:
                ambiguous = ClinicLink.objects.filter(
                    enrollment_id=enrollment_id,
                    session_id=session_id,
                    source_type__isnull=True,
                    resolved_at__isnull=True,
                ).count()
                if ambiguous:
                    logger.warning(
                        "clinic_resolution: HOMEWORK_PASS skipped %d legacy link(s) "
                        "without matching meta.homework_id=%s (enrollment=%s, session=%s) — "
                        "manual backfill recommended",
                        ambiguous, homework_id, enrollment_id, session_id,
                    )

        if count > 0:
            logger.info(
                "clinic_resolution: HOMEWORK_PASS resolved %d links "
                "(enrollment=%s, session=%s, homework=%s)",
                count, enrollment_id, session_id, homework_id,
            )
            _eid, _sid = enrollment_id, session_id
            transaction.on_commit(lambda: _send_resolution_notification(_eid, _sid, "HOMEWORK_PASS"))
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

        now = timezone.now()
        _append_history(link, action="resolve_manual", at=now)
        link.resolved_at = now
        link.resolution_type = ClinicLink.ResolutionType.MANUAL_OVERRIDE
        link.resolution_evidence = {"user_id": user_id, "memo": memo}
        if memo:
            link.memo = memo
        link.save(update_fields=[
            "resolved_at", "resolution_type", "resolution_evidence",
            "resolution_history", "memo", "updated_at",
        ])

        logger.info("clinic_resolution: MANUAL_OVERRIDE (link=%s, user=%s)", clinic_link_id, user_id)
        _eid, _sid = link.enrollment_id, link.session_id
        transaction.on_commit(lambda: _send_resolution_notification(_eid, _sid, "MANUAL_OVERRIDE"))
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

        now = timezone.now()
        _append_history(link, action="waive", at=now)
        link.resolved_at = now
        link.resolution_type = ClinicLink.ResolutionType.WAIVED
        link.resolution_evidence = {"user_id": user_id, "memo": memo}
        if memo:
            link.memo = memo
        link.save(update_fields=[
            "resolved_at", "resolution_type", "resolution_evidence",
            "resolution_history", "memo", "updated_at",
        ])

        logger.info("clinic_resolution: WAIVED (link=%s, user=%s)", clinic_link_id, user_id)
        _eid, _sid = link.enrollment_id, link.session_id
        transaction.on_commit(lambda: _send_resolution_notification(_eid, _sid, "WAIVED"))
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

        # 이전 해소 정보를 history에 보존한 후 리셋
        _append_history(link, action="unresolve")
        link.resolved_at = None
        link.resolution_type = None
        link.resolution_evidence = None
        link.save(update_fields=[
            "resolved_at", "resolution_type", "resolution_evidence",
            "resolution_history", "updated_at",
        ])

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
        현재 link를 CARRIED_OVER로 닫고, 새 link를 cycle_no+1로 생성.
        """
        try:
            link = ClinicLink.objects.select_for_update().get(
                id=clinic_link_id,
                resolved_at__isnull=True,
            )
        except ClinicLink.DoesNotExist:
            logger.warning("clinic_resolution: carry_over failed, link not found (id=%s)", clinic_link_id)
            return None

        # Close current with CARRIED_OVER resolution (별도 enum — WAIVED/면제와 구분)
        now = timezone.now()
        link.resolved_at = now
        link.resolution_type = ClinicLink.ResolutionType.CARRIED_OVER
        link.resolution_evidence = {"carried_over": True, "carried_at": now.isoformat()}
        _append_history(link, action="carry_over", at=now)
        link.save(update_fields=[
            "resolved_at", "resolution_type", "resolution_evidence",
            "resolution_history", "updated_at",
        ])

        # Create next cycle (source + tenant 전파)
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
            tenant_id=link.tenant_id,
        )

        logger.info(
            "clinic_resolution: CARRIED_OVER (old=%s→new=%s, cycle=%d→%d)",
            clinic_link_id, new_link.id, link.cycle_no, new_link.cycle_no,
        )
        return new_link
