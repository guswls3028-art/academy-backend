# apps/domains/progress/services/session_calculator.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from django.utils import timezone
from django.db import transaction
from django.db.models import Count

from apps.domains.progress.models import SessionProgress, ProgressPolicy
from apps.domains.lectures.models import Session

from apps.domains.results.models import Result, ExamAttempt
from apps.domains.exams.models import Exam

# ✅ 단일 진실: Session↔Exam 매핑
from apps.domains.results.utils.session_exam import get_exam_ids_for_session


class SessionProgressCalculator:
    """
    차시(Session) 단위 진행 계산기

    ✅ 핵심 원칙(1 Session : N Exams):
    - SessionProgress는 특정 Exam 하나의 점수에 의존하지 않는다.
    - 반드시 Result 테이블(대표 스냅샷)들을 모아서 집계한다.
    - 집계 전략(MAX/AVG/LATEST)과 pass 기준 출처(POLICY/EXAM)는 ProgressPolicy가 단일 진실.
    """

    @staticmethod
    def _get_or_create_policy(session: Session) -> ProgressPolicy:
        policy, _ = ProgressPolicy.objects.get_or_create(
            lecture=session.lecture,
            defaults={
                "video_required_rate": 90,
                "exam_start_session_order": 1,
                "exam_end_session_order": 9999,
                "exam_pass_score": 60.0,
                "exam_aggregate_strategy": ProgressPolicy.ExamAggregateStrategy.MAX,
                "exam_pass_source": ProgressPolicy.ExamPassSource.EXAM,
                "homework_start_session_order": 1,
                "homework_end_session_order": 9999,
                "homework_pass_type": ProgressPolicy.HomeworkPassType.TEACHER_APPROVAL,
            },
        )
        return policy

    @staticmethod
    def _pick_latest(results: List[Result]) -> Optional[Result]:
        if not results:
            return None
        return sorted(
            results,
            key=lambda r: (
                r.submitted_at is not None,
                r.submitted_at or timezone.datetime.min.replace(tzinfo=timezone.get_current_timezone()),
                r.id,
            ),
        )[-1]

    @staticmethod
    def _safe_float(v: Any, default: float = 0.0) -> float:
        try:
            return float(v)
        except Exception:
            return default

    @classmethod
    def _aggregate_exam_results(
        cls,
        *,
        enrollment_id: int,
        session: Session,
        policy: ProgressPolicy,
    ) -> Tuple[bool, Optional[float], bool, Dict[str, Any]]:
        exam_ids = get_exam_ids_for_session(session)

        if not exam_ids:
            meta = {
                "strategy": str(policy.exam_aggregate_strategy),
                "pass_source": str(policy.exam_pass_source),
                "exams": [],
                "note": "no_exams_in_session",
            }
            return False, None, True, meta

        results = list(
            Result.objects.filter(
                target_type="exam",
                enrollment_id=int(enrollment_id),
                target_id__in=[int(x) for x in exam_ids],
            )
        )

        if not results:
            meta = {
                "strategy": str(policy.exam_aggregate_strategy),
                "pass_source": str(policy.exam_pass_source),
                "exams": [
                    {
                        "exam_id": int(eid),
                        "score": None,
                        "max_score": None,
                        "pass_score": None,
                        "passed": False,
                        "submitted_at": None,
                        "attempt_count": 0,
                    }
                    for eid in exam_ids
                ],
                "note": "no_results",
            }
            return False, None, False, meta

        exam_attempted = True

        exams = {e.id: e for e in Exam.objects.filter(id__in=[int(x) for x in exam_ids])}

        # ✅ Attempt count is authoritative from ExamAttempt
        attempt_counts = {
            int(row["exam_id"]): int(row["cnt"] or 0)
            for row in (
                ExamAttempt.objects.filter(
                    exam_id__in=[int(x) for x in exam_ids],
                    enrollment_id=int(enrollment_id),
                )
                .values("exam_id")
                .annotate(cnt=Count("id"))
            )
        }

        # 미응시(NOT_SUBMITTED) ExamAttempt 식별
        not_submitted_exam_ids: set = set()
        for ea in ExamAttempt.objects.filter(
            exam_id__in=[int(x) for x in exam_ids],
            enrollment_id=int(enrollment_id),
        ):
            meta = ea.meta if isinstance(ea.meta, dict) else {}
            if meta.get("status") == "NOT_SUBMITTED":
                not_submitted_exam_ids.add(int(ea.exam_id))

        per_exam_rows: List[Dict[str, Any]] = []
        for r in results:
            ex = exams.get(int(r.target_id))

            exam_pass_score = cls._safe_float(getattr(ex, "pass_score", None), default=0.0) if ex else 0.0
            policy_pass_score = cls._safe_float(getattr(policy, "exam_pass_score", 0.0), default=0.0)

            pass_score = (
                policy_pass_score
                if policy.exam_pass_source == ProgressPolicy.ExamPassSource.POLICY
                else exam_pass_score
            )
            score = cls._safe_float(r.total_score, default=0.0)

            # 미응시 학생은 pass_score와 무관하게 불합격
            is_not_submitted = int(r.target_id) in not_submitted_exam_ids
            if is_not_submitted:
                passed_value = False
            elif pass_score > 0:
                passed_value = bool(score >= float(pass_score))
            else:
                passed_value = True  # pass_score=0 + 응시완료 → 기준 없음, 통과

            per_exam_rows.append(
                {
                    "exam_id": int(r.target_id),
                    "score": score,
                    "max_score": cls._safe_float(r.max_score, default=0.0),
                    "pass_score": float(pass_score),
                    "passed": passed_value,
                    "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
                    "attempt_count": int(attempt_counts.get(int(r.target_id), 0)),
                }
            )

        strategy = policy.exam_aggregate_strategy

        aggregate_score: Optional[float] = None
        selected_pass_score: float = cls._safe_float(policy.exam_pass_score, 0.0)

        if strategy == ProgressPolicy.ExamAggregateStrategy.MAX:
            best = max(per_exam_rows, key=lambda x: cls._safe_float(x.get("score"), 0.0))
            aggregate_score = cls._safe_float(best.get("score"), 0.0)
            selected_pass_score = cls._safe_float(best.get("pass_score"), 0.0)

        elif strategy == ProgressPolicy.ExamAggregateStrategy.AVG:
            scores = [cls._safe_float(x.get("score"), 0.0) for x in per_exam_rows]
            aggregate_score = (sum(scores) / len(scores)) if scores else 0.0

            if policy.exam_pass_source == ProgressPolicy.ExamPassSource.EXAM:
                ps = [cls._safe_float(x.get("pass_score"), 0.0) for x in per_exam_rows]
                selected_pass_score = (sum(ps) / len(ps)) if ps else 0.0
            else:
                selected_pass_score = cls._safe_float(policy.exam_pass_score, 0.0)

        elif strategy == ProgressPolicy.ExamAggregateStrategy.LATEST:
            latest = cls._pick_latest(results)
            if latest is None:
                aggregate_score = 0.0
                selected_pass_score = cls._safe_float(policy.exam_pass_score, 0.0)
            else:
                row = next(
                    (x for x in per_exam_rows if int(x["exam_id"]) == int(latest.target_id)),
                    None,
                )
                aggregate_score = cls._safe_float(latest.total_score, 0.0)
                selected_pass_score = cls._safe_float(
                    (row.get("pass_score") if row else policy.exam_pass_score),
                    0.0,
                )

        else:
            best = max(per_exam_rows, key=lambda x: cls._safe_float(x.get("score"), 0.0))
            aggregate_score = cls._safe_float(best.get("score"), 0.0)
            selected_pass_score = cls._safe_float(best.get("pass_score"), 0.0)

        # ⚠️ 세션 완료 판정은 "개별 시험 모두 통과" 기준 (드리프트 해소).
        # aggregate_score는 성적 표시용(MAX/AVG/LATEST), 통과 여부는 전수 검사.
        # 세션에 시험 여러 개인 경우 일부만 통과해도 세션 완료가 되던 문제를 차단.
        all_not_submitted = all(not x["passed"] for x in per_exam_rows) and all(
            int(x["exam_id"]) in not_submitted_exam_ids for x in per_exam_rows
        )
        if all_not_submitted:
            exam_passed = False
        else:
            # 모든 개별 시험이 passed=True여야 세션 완료
            exam_passed = bool(per_exam_rows) and all(bool(x["passed"]) for x in per_exam_rows)

        meta = {
            "strategy": str(strategy),
            "pass_source": str(policy.exam_pass_source),
            "aggregate_pass_score": float(selected_pass_score),
            "all_passed": exam_passed,  # 명시: 모든 개별 시험 통과 여부
            "exams": per_exam_rows,
        }

        return True, aggregate_score, exam_passed, meta

    @staticmethod
    def calculate(
        *,
        enrollment_id: int,
        session: Session,
        attendance_type: str,
        video_progress_rate: int = 0,
        homework_submitted: bool = False,
    ) -> SessionProgress:
        policy = SessionProgressCalculator._get_or_create_policy(session)

        obj, _ = SessionProgress.objects.get_or_create(
            enrollment_id=enrollment_id,
            session=session,
        )

        obj.attendance_type = attendance_type
        obj.video_progress_rate = int(video_progress_rate or 0)

        if attendance_type == SessionProgress.AttendanceType.OFFLINE:
            obj.video_completed = True
        else:
            obj.video_completed = obj.video_progress_rate >= int(policy.video_required_rate)

        in_exam_range = bool(policy.exam_start_session_order <= session.order <= policy.exam_end_session_order)
        if in_exam_range:
            attempted, agg_score, passed, exam_meta = SessionProgressCalculator._aggregate_exam_results(
                enrollment_id=enrollment_id,
                session=session,
                policy=policy,
            )
            obj.exam_attempted = bool(attempted)
            obj.exam_aggregate_score = agg_score
            obj.exam_passed = bool(passed)
            obj.exam_meta = exam_meta
        else:
            obj.exam_attempted = False
            obj.exam_aggregate_score = None
            obj.exam_passed = True
            obj.exam_meta = {
                "strategy": str(policy.exam_aggregate_strategy),
                "pass_source": str(policy.exam_pass_source),
                "exams": [],
                "note": "out_of_exam_range",
            }

        in_hw_range = bool(policy.homework_start_session_order <= session.order <= policy.homework_end_session_order)
        if in_hw_range:
            obj.homework_submitted = bool(homework_submitted)

            if policy.homework_pass_type == ProgressPolicy.HomeworkPassType.SUBMIT:
                obj.homework_passed = bool(homework_submitted)

            elif policy.homework_pass_type == ProgressPolicy.HomeworkPassType.SCORE:
                # HomeworkScore를 직접 쿼리 (exam과 동일 패턴)
                from apps.domains.homework_results.models import HomeworkScore
                obj.homework_passed = HomeworkScore.objects.filter(
                    enrollment_id=enrollment_id,
                    session_id=session.id,
                    attempt_index=1,
                    passed=True,
                ).exists()

            elif policy.homework_pass_type == ProgressPolicy.HomeworkPassType.TEACHER_APPROVAL:
                from apps.domains.homework_results.models import HomeworkScore
                obj.homework_passed = HomeworkScore.objects.filter(
                    enrollment_id=enrollment_id,
                    session_id=session.id,
                    attempt_index=1,
                    teacher_approved=True,
                ).exists()
        else:
            obj.homework_passed = True

        obj.completed = bool(obj.video_completed and obj.exam_passed and obj.homework_passed)

        # ⚠️ completed_at은 "최초 완료 시점"으로 불변화.
        # 점수 수정/대표 attempt 교체로 completed가 False로 회귀해도
        # 지우지 않음(감사 이력 보존). 현재 완료 여부는 `completed` 플래그로만 판단.
        if obj.completed and not obj.completed_at:
            obj.completed_at = timezone.now()

        obj.calculated_at = timezone.now()
        obj.save()

        return obj
