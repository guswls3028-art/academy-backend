# apps/domains/progress/services/progress_pipeline.py
from __future__ import annotations

import logging
from typing import Optional, List

from django.db import transaction
from django.utils import timezone

from apps.domains.progress.services.session_calculator import SessionProgressCalculator
from apps.domains.progress.services.lecture_calculator import LectureProgressCalculator
from apps.domains.progress.services.risk_evaluator import RiskEvaluator
from apps.domains.progress.services.clinic_trigger_service import ClinicTriggerService

logger = logging.getLogger(__name__)


class ProgressPipelineService:
    """
    ✅ Synchronous progress pipeline (Enterprise-grade)

    Properties:
    - Queue-less (no Celery)
    - Idempotent (safe to call repeatedly)
    - Defensive (handles partial data without crashing the entire system)
    """

    # ---------------------------------------------------------
    # Public entry
    # ---------------------------------------------------------
    @transaction.atomic
    def apply(self, *, exam_id: Optional[int] = None, submission_id: Optional[int] = None) -> None:
        """
        Entry point.
        - If submission_id is provided, it will try to compute progress based on that submission's target.
        - If exam_id is provided, it will compute progress for sessions that include that exam.
        """
        if submission_id is not None:
            self._apply_by_submission(submission_id=int(submission_id))
            return

        if exam_id is not None:
            self._apply_by_exam(exam_id=int(exam_id))
            return

        logger.warning("ProgressPipelineService.apply called with no args")
        return

    # ---------------------------------------------------------
    # Internal: submission-based
    # ---------------------------------------------------------
    def _apply_by_submission(self, *, submission_id: int) -> None:
        # imports inside to avoid cycles
        from apps.domains.submissions.models import Submission  # type: ignore
        from apps.domains.lectures.models import Session, Lecture  # type: ignore

        sub = Submission.objects.select_for_update().filter(id=int(submission_id)).first()
        if not sub:
            logger.warning("progress pipeline: submission not found (id=%s)", submission_id)
            return

        enroll_id = getattr(sub, "enrollment_id", None)
        if not enroll_id:
            logger.warning("progress pipeline: submission has no enrollment_id (id=%s)", submission_id)
            return

        # 현재 시스템에서 시험/숙제 모두 target_id를 가진다고 가정
        target_type = str(getattr(sub, "target_type", "") or "")
        target_id = int(getattr(sub, "target_id", 0) or 0)
        if not target_type or not target_id:
            logger.warning(
                "progress pipeline: invalid target on submission (id=%s, type=%s, target_id=%s)",
                submission_id, target_type, target_id
            )
            return

        # 시험인 경우: exam_id 기반으로 세션 찾기
        if target_type == "exam":
            sessions = self._find_sessions_for_exam(exam_id=target_id)
            if not sessions:
                logger.warning("progress pipeline: no sessions matched exam_id=%s (submission_id=%s)", target_id, submission_id)
                return

            for s in sessions:
                self._recompute_for_session(enrollment_id=int(enroll_id), session=s, exam_id=target_id)

            return

        # 숙제인 경우: homework는 target_id가 homework_id일 수 있음.
        # 여기서는 "숙제 제출 여부는 homework 도메인/교사 승인과 결합"이 필요하므로
        # ✅ 보수적으로: session을 찾을 수 있으면 SessionProgressCalculator에 homework flags를 넣어서 업데이트,
        # 없으면 로그만 남기고 종료.
        if target_type == "homework":
            sessions = self._find_sessions_for_homework(homework_id=target_id)
            if not sessions:
                logger.info("progress pipeline: no sessions matched homework_id=%s (submission_id=%s)", target_id, submission_id)
                return

            for s in sessions:
                # homework는 정책이 다양하므로 최소한 제출 사실만 반영 (승인/점수는 별도 이벤트로 recompute)
                self._recompute_for_session(enrollment_id=int(enroll_id), session=s, exam_id=None, homework_submitted=True)

            return

        logger.info("progress pipeline: unsupported target_type=%s (submission_id=%s)", target_type, submission_id)

    # ---------------------------------------------------------
    # Internal: exam-based
    # ---------------------------------------------------------
    def _apply_by_exam(self, *, exam_id: int) -> None:
        from apps.domains.results.models import Result  # type: ignore

        # 누가 봐도 "이 시험을 본 사람들"만 recompute
        enroll_ids = (
            Result.objects.filter(target_type="exam", target_id=int(exam_id))
            .values_list("enrollment_id", flat=True)
            .distinct()
        )
        enroll_ids = [int(x) for x in enroll_ids if x is not None]

        if not enroll_ids:
            logger.info("progress pipeline: no enrollments for exam_id=%s", exam_id)
            return

        sessions = self._find_sessions_for_exam(exam_id=exam_id)
        if not sessions:
            logger.warning("progress pipeline: no sessions matched exam_id=%s", exam_id)
            return

        for enroll_id in enroll_ids:
            for s in sessions:
                self._recompute_for_session(enrollment_id=int(enroll_id), session=s, exam_id=exam_id)

    # ---------------------------------------------------------
    # Mapping helpers (defensive)
    # ---------------------------------------------------------
    def _find_sessions_for_exam(self, *, exam_id: int) -> List["Session"]:
        """
        ✅ 단일 진실이 있다면 그 유틸을 사용하고,
        없으면 fallback 시도.
        """
        from apps.domains.lectures.models import Session  # type: ignore

        # 1) Single-source utility (recommended if exists)
        try:
            from apps.domains.results.utils.session_exam import get_session_ids_for_exam  # type: ignore
            session_ids = get_session_ids_for_exam(int(exam_id))
            if session_ids:
                return list(Session.objects.filter(id__in=[int(x) for x in session_ids]).select_related("lecture"))
        except Exception:
            # ignore and fallback
            pass

        # 2) Fallback: if legacy relation exists (some deployments still have it)
        try:
            # e.g., Session.exams M2M (legacy)
            return list(Session.objects.filter(exams__id=int(exam_id)).select_related("lecture").distinct())
        except Exception:
            return []

    def _find_sessions_for_homework(self, *, homework_id: int) -> List["Session"]:
        """
        Homework ↔ Session mapping is project-specific.
        If you have a SSOT util, plug it here.
        """
        from apps.domains.lectures.models import Session  # type: ignore

        try:
            from apps.domains.results.utils.session_homework import get_session_ids_for_homework  # type: ignore
            session_ids = get_session_ids_for_homework(int(homework_id))
            if session_ids:
                return list(Session.objects.filter(id__in=[int(x) for x in session_ids]).select_related("lecture"))
        except Exception:
            pass

        # fallback (if legacy schema)
        try:
            return list(Session.objects.filter(homework__id=int(homework_id)).select_related("lecture").distinct())
        except Exception:
            return []

    # ---------------------------------------------------------
    # Recompute core
    # ---------------------------------------------------------
    def _recompute_for_session(
        self,
        *,
        enrollment_id: int,
        session,
        exam_id: Optional[int] = None,
        homework_submitted: bool = False,
    ) -> None:
        """
        Recompute:
        1) SessionProgress (exam aggregate uses Result SSOT)
        2) Clinic triggers (failed / exam risk)
        3) LectureProgress + Risk evaluation
        """
        # attendance/video/homework inputs are outside scope; keep conservative defaults.
        sp = SessionProgressCalculator.calculate(
            enrollment_id=int(enrollment_id),
            session=session,
            attendance_type="online",
            video_progress_rate=0,
            homework_submitted=bool(homework_submitted),
            homework_teacher_approved=False,
        )

        # failed trigger (idempotent via get_or_create in service)
        ClinicTriggerService.auto_create_if_failed(sp)

        # exam risk trigger: only if we know exam_id
        if exam_id is not None:
            try:
                ClinicTriggerService.auto_create_if_exam_risk(
                    enrollment_id=int(enrollment_id),
                    session=session,
                    exam_id=int(exam_id),
                )
            except Exception:
                logger.exception("clinic exam risk trigger failed (enroll=%s, exam=%s, session=%s)", enrollment_id, exam_id, session.id)

        # lecture aggregates + risk
        try:
            lp = LectureProgressCalculator.calculate(enrollment_id=int(enrollment_id), lecture=session.lecture)
            RiskEvaluator.evaluate(lp)
        except Exception:
            logger.exception("lecture/risk recompute failed (enroll=%s, lecture=%s)", enrollment_id, getattr(session, "lecture_id", None))
            raise
