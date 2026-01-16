# apps/domains/results/services/session_score_summary_service.py
from __future__ import annotations

from django.db.models import Avg, Min, Max, Count

from apps.domains.results.models import ExamAttempt
from apps.domains.progress.models import SessionProgress
from apps.domains.progress.models import ClinicLink
from apps.domains.lectures.models import Session

# ✅ 단일 진실 유틸
from apps.domains.results.utils.session_exam import get_exam_ids_for_session
from apps.domains.results.utils.result_queries import latest_results_per_enrollment


class SessionScoreSummaryService:
    """
    ✅ Session 단위 성적 통계 (운영/대시보드)

    단일 진실 규칙:
    - 점수 통계: Result(단, enrollment 중복 방어 적용)
    - 세션 통과율: SessionProgress.completed(혹은 정책에 따라 exam_passed) 중 무엇인지 '정의'가 필요하지만
      기존 원본은 completed를 사용했으므로 원본 의미를 존중한다.
    - 클리닉: ClinicLink (is_auto=True, enrollment distinct)

    ⚠️ 세션1:시험N 구조 반영:
    - session에 연결된 exam_id들을 모두 가져와서 통계를 만든다.
    - 다만 "세션 전체 점수"를 1개 숫자로 만들 때는 집계 전략이 필요함.
      이 서비스는 "세션 운영 통계" 성격이므로:
        - 점수 집계는 우선 exams 전체 Result를 합쳐 평균/최소/최대를 구하는 보수적 방식으로 제공.
      (정교한 전략은 AdminSessionExamsSummaryView에서 exam별로 제공하는 것이 정석)
    """

    @staticmethod
    def build(*, session_id: int) -> dict:
        EMPTY_SUMMARY = {
            "participant_count": 0,
            "avg_score": 0.0,
            "min_score": 0.0,
            "max_score": 0.0,
            "pass_rate": 0.0,
            "clinic_rate": 0.0,
            "attempt_stats": {
                "avg_attempts": 0.0,
                "retake_ratio": 0.0,
            },
        }

        session = Session.objects.filter(id=int(session_id)).first()
        if not session:
            return EMPTY_SUMMARY

        exam_ids = get_exam_ids_for_session(session)
        if not exam_ids:
            # 세션에 시험이 없으면 점수 통계는 0, pass/clinic은 progress로만 판단 가능
            progresses = SessionProgress.objects.filter(session=session)
            participant_count = progresses.count()
            pass_count = progresses.filter(completed=True).count()
            clinic_count = (
                ClinicLink.objects.filter(session=session, is_auto=True)
                .values("enrollment_id").distinct().count()
            )
            return {
                **EMPTY_SUMMARY,
                "participant_count": int(participant_count),
                "pass_rate": round((pass_count / participant_count), 4) if participant_count else 0.0,
                "clinic_rate": round((clinic_count / participant_count), 4) if participant_count else 0.0,
            }

        # -------------------------------------------------
        # participant 모수: SessionProgress 기준(원본 존중)
        # -------------------------------------------------
        progresses = SessionProgress.objects.filter(session=session)
        participant_count = progresses.count()

        # -------------------------------------------------
        # pass_rate: 원본은 SessionProgress.completed 기준
        # -------------------------------------------------
        pass_count = progresses.filter(completed=True).count()
        pass_rate = (pass_count / participant_count) if participant_count else 0.0

        # -------------------------------------------------
        # clinic_rate: ClinicLink 기준 단일화
        # -------------------------------------------------
        clinic_count = (
            ClinicLink.objects.filter(session=session, is_auto=True)
            .values("enrollment_id")
            .distinct()
            .count()
        )
        clinic_rate = (clinic_count / participant_count) if participant_count else 0.0

        # -------------------------------------------------
        # 점수 통계:
        # - 세션에 연결된 모든 시험의 Result를 모아서 통계
        # - enrollment 중복 방어: exam별 latest_results_per_enrollment 적용 후 합치기
        # -------------------------------------------------
        all_results = []
        for exid in exam_ids:
            rs = list(latest_results_per_enrollment(target_type="exam", target_id=int(exid)))
            all_results.extend(rs)

        if not all_results:
            score_summary = {"avg_score": 0.0, "min_score": 0.0, "max_score": 0.0}
        else:
            scores = [float(r.total_score or 0.0) for r in all_results]
            score_summary = {
                "avg_score": (sum(scores) / len(scores)) if scores else 0.0,
                "min_score": min(scores) if scores else 0.0,
                "max_score": max(scores) if scores else 0.0,
            }

        # -------------------------------------------------
        # Attempt 통계(재시험 비율):
        # - 세션에 연결된 모든 시험을 대상으로 attempt 통계
        # -------------------------------------------------
        attempts = ExamAttempt.objects.filter(exam_id__in=[int(x) for x in exam_ids])

        per_enrollment = (
            attempts.values("enrollment_id")
            .annotate(cnt=Count("id"))
        )

        total_attempts = sum(int(r["cnt"] or 0) for r in per_enrollment)
        retake_users = sum(1 for r in per_enrollment if int(r["cnt"] or 0) > 1)

        avg_attempts = (total_attempts / participant_count) if participant_count else 0.0
        retake_ratio = (retake_users / participant_count) if participant_count else 0.0

        return {
            "participant_count": int(participant_count),
            "avg_score": float(score_summary["avg_score"]),
            "min_score": float(score_summary["min_score"]),
            "max_score": float(score_summary["max_score"]),
            "pass_rate": round(float(pass_rate), 4),
            "clinic_rate": round(float(clinic_rate), 4),
            "attempt_stats": {
                "avg_attempts": round(float(avg_attempts), 2),
                "retake_ratio": round(float(retake_ratio), 4),
            },
        }
