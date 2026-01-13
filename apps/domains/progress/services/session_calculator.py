# apps/domains/progress/services/session_calculator.py
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from django.utils import timezone

from apps.domains.progress.models import SessionProgress, ProgressPolicy
from apps.domains.lectures.models import Session

from apps.domains.results.models import Result
from apps.domains.exams.models import Exam


class SessionProgressCalculator:
    """
    차시(Session) 단위 진행 계산기

    ✅ 1:N 시험 구조 원칙 준수:
    - submission.exam_score 같은 단일 필드에 의존하지 않는다.
    - 반드시 Result 테이블에서:
        enrollment_id + (session에 연결된 모든 exam_id)
      를 조회해 집계한다.
    """

    # -----------------------------
    # Policy (lazy create)
    # -----------------------------
    @staticmethod
    def _get_or_create_policy(session: Session) -> ProgressPolicy:
        policy, _ = ProgressPolicy.objects.get_or_create(
            lecture=session.lecture,
            defaults={
                "video_required_rate": 90,
                "exam_start_session_order": 2,
                "exam_end_session_order": 9999,
                "exam_pass_score": 60.0,
                "exam_aggregate_strategy": ProgressPolicy.ExamAggregateStrategy.MAX,
                "exam_pass_source": ProgressPolicy.ExamPassSource.EXAM,
                "homework_start_session_order": 2,
                "homework_end_session_order": 9999,
                "homework_pass_type": ProgressPolicy.HomeworkPassType.TEACHER_APPROVAL,
            },
        )
        return policy

    # -----------------------------
    # Session ↔ Exam(s) resolver
    # -----------------------------
    @staticmethod
    def _has_relation(model, name: str) -> bool:
        try:
            return any(getattr(f, "name", None) == name for f in model._meta.get_fields())
        except Exception:
            return False

    @classmethod
    def _get_exam_ids_for_session(cls, session: Session) -> List[int]:
        """
        프로젝트마다 Session↔Exam 관계가 다를 수 있으므로 방어적으로 exam_id 목록을 구한다.

        지원 케이스:
        - Session.exam_id (FK)
        - Session.exams (M2M)
        - Exam.sessions reverse (M2M)
        - Exam.session reverse (FK/1:1)
        """
        # 1) Session에 exam FK가 있는 경우
        exam_id = getattr(session, "exam_id", None)
        if exam_id:
            return [int(exam_id)]

        # 2) Session.exams (M2M)
        if cls._has_relation(Session, "exams"):
            try:
                return list(session.exams.values_list("id", flat=True))
            except Exception:
                pass

        # 3) Exam.session / Exam.sessions 를 통해 역으로 찾기
        # (안전하게 Exam 전체에서 세션 매핑)
        qs = Exam.objects.all()

        if cls._has_relation(Exam, "sessions"):
            qs = qs.filter(sessions__id=int(session.id))
            return list(qs.values_list("id", flat=True))

        if cls._has_relation(Exam, "session"):
            qs = qs.filter(session__id=int(session.id))
            return list(qs.values_list("id", flat=True))

        return []

    # -----------------------------
    # Exam aggregate logic
    # -----------------------------
    @staticmethod
    def _pick_latest(results: List[Result]) -> Optional[Result]:
        if not results:
            return None
        # submitted_at이 null일 수 있으니 (null은 과거 취급) 방어 정렬
        return sorted(
            results,
            key=lambda r: (r.submitted_at is not None, r.submitted_at or timezone.datetime.min.replace(tzinfo=timezone.get_current_timezone())),
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
        """
        반환:
          exam_attempted, aggregate_score, exam_passed, exam_meta
        """
        exam_ids = cls._get_exam_ids_for_session(session)

        # 시험이 아예 없는 session
        if not exam_ids:
            meta = {
                "strategy": str(policy.exam_aggregate_strategy),
                "pass_source": str(policy.exam_pass_source),
                "exams": [],
            }
            return False, None, True, meta  # 시험 없음 => progress상 시험 통과로 취급(정책상 비대상)

        # Result 조회 (대표 attempt 스냅샷)
        results = list(
            Result.objects.filter(
                target_type="exam",
                enrollment_id=int(enrollment_id),
                target_id__in=[int(x) for x in exam_ids],
            )
        )

        # Result가 하나도 없으면 "시험은 있었는데 미응시"
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
                    }
                    for eid in exam_ids
                ],
            }
            return False, None, False, meta

        exam_attempted = True

        # exam별 pass_score 로딩 (EXAM pass_source 대응)
        exams = {
            e.id: e
            for e in Exam.objects.filter(id__in=[int(x) for x in exam_ids])
        }

        # 시험별 row 구성
        per_exam_rows: List[Dict[str, Any]] = []
        for r in results:
            ex = exams.get(int(r.target_id))
            exam_pass_score = cls._safe_float(getattr(ex, "pass_score", None), default=0.0) if ex else 0.0
            policy_pass_score = cls._safe_float(getattr(policy, "exam_pass_score", 0.0), default=0.0)

            pass_score = policy_pass_score if policy.exam_pass_source == ProgressPolicy.ExamPassSource.POLICY else exam_pass_score
            score = cls._safe_float(r.total_score, default=0.0)

            per_exam_rows.append({
                "exam_id": int(r.target_id),
                "score": score,
                "max_score": cls._safe_float(r.max_score, default=0.0),
                "pass_score": float(pass_score),
                "passed": bool(score >= float(pass_score)),
                "submitted_at": r.submitted_at,
                "submitted_count": rs.count(),
            })

        # 집계 점수 계산
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

            # AVG에서 pass_score를 EXAM 기준으로 하려면 "평균 pass_score"로 두는게 합리적(정책화 가능)
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
                row = next((x for x in per_exam_rows if int(x["exam_id"]) == int(latest.target_id)), None)
                aggregate_score = cls._safe_float(latest.total_score, 0.0)
                selected_pass_score = cls._safe_float(row.get("pass_score") if row else policy.exam_pass_score, 0.0)

        else:
            # 안전 fallback: MAX
            best = max(per_exam_rows, key=lambda x: cls._safe_float(x.get("score"), 0.0))
            aggregate_score = cls._safe_float(best.get("score"), 0.0)
            selected_pass_score = cls._safe_float(best.get("pass_score"), 0.0)

        exam_passed = bool((aggregate_score or 0.0) >= float(selected_pass_score))

        meta = {
            "strategy": str(strategy),
            "pass_source": str(policy.exam_pass_source),
            "aggregate_pass_score": float(selected_pass_score),
            "exams": per_exam_rows,
        }

        return True, aggregate_score, exam_passed, meta

    # -----------------------------
    # Main calculate
    # -----------------------------
    @staticmethod
    def calculate(
        *,
        enrollment_id: int,
        session: Session,
        attendance_type: str,
        video_progress_rate: int = 0,
        homework_submitted: bool = False,
        homework_teacher_approved: bool = False,
    ) -> SessionProgress:
        """
        외부 도메인 값들을 받아서 SessionProgress를 계산/업데이트

        ✅ 시험은 Result 기반으로만 계산한다.
        """

        policy = SessionProgressCalculator._get_or_create_policy(session)

        obj, _ = SessionProgress.objects.get_or_create(
            enrollment_id=enrollment_id,
            session=session,
        )

        # ----------------------
        # Attendance / Video
        # ----------------------
        obj.attendance_type = attendance_type
        obj.video_progress_rate = int(video_progress_rate or 0)

        if attendance_type == SessionProgress.AttendanceType.OFFLINE:
            obj.video_completed = True
        else:
            obj.video_completed = obj.video_progress_rate >= int(policy.video_required_rate)

        # ----------------------
        # Exam aggregate (Result 기반)
        # ----------------------
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
            # 시험 비대상 주차
            obj.exam_attempted = False
            obj.exam_aggregate_score = None
            obj.exam_passed = True
            obj.exam_meta = {
                "strategy": str(policy.exam_aggregate_strategy),
                "pass_source": str(policy.exam_pass_source),
                "exams": [],
                "note": "out_of_exam_range",
            }

        # ----------------------
        # Homework
        # ----------------------
        in_hw_range = bool(policy.homework_start_session_order <= session.order <= policy.homework_end_session_order)
        if in_hw_range:
            obj.homework_submitted = bool(homework_submitted)

            if policy.homework_pass_type == ProgressPolicy.HomeworkPassType.SUBMIT:
                obj.homework_passed = bool(homework_submitted)

            elif policy.homework_pass_type == ProgressPolicy.HomeworkPassType.SCORE:
                # (레거시) 점수 기반이 필요하면 별도 필드로 확장
                obj.homework_passed = bool(homework_teacher_approved)

            elif policy.homework_pass_type == ProgressPolicy.HomeworkPassType.TEACHER_APPROVAL:
                obj.homework_passed = bool(homework_teacher_approved)
        else:
            obj.homework_passed = True

        # ----------------------
        # Final Completion
        # ----------------------
        obj.completed = bool(
            obj.video_completed
            and obj.exam_passed
            and obj.homework_passed
        )

        if obj.completed and not obj.completed_at:
            obj.completed_at = timezone.now()

        obj.calculated_at = timezone.now()
        obj.save()

        return obj
