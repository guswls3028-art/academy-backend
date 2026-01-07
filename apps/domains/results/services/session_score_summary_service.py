# PATH: apps/domains/results/services/session_score_summary_service.py

from __future__ import annotations

from django.db.models import Avg, Min, Max, Count

from apps.domains.results.models import Result, ExamAttempt
from apps.domains.progress.models import SessionProgress

# ======================================================
# 🔧 PATCH: Clinic은 Progress가 아니라 ClinicLink 도메인
# - SessionProgress.clinic_required 같은 필드가 없다는 계약에 맞춤
# ======================================================
from apps.domains.progress.models import ClinicLink  # ✅ PATCH

from apps.domains.lectures.models import Session


class SessionScoreSummaryService:
    """
    ✅ Session 단위 성적 통계 (results 기준 단일 진실)

    사용 근거:
    - 점수: Result (대표 attempt 스냅샷)
    - 통과: SessionProgress (completed 기준)  ✅ PATCH
    - 클리닉: ClinicLink (자동 트리거 기준) ✅ PATCH
    - 재시험: ExamAttempt

    ⚠️ PATCH(설계 정합성):
    - Session은 단일 exam만 가짐 (Session.exam FK)  ✅ PATCH
    """

    @staticmethod
    def build(*, session_id: int) -> dict:
        # -----------------------------
        # EMPTY 응답 (기존 유지)
        # -----------------------------
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

        # =====================================================
        # 🔥 CRITICAL PATCH #1:
        # Session ↔ Exam 관계 오류 수정
        #
        # 기존 코드는 session.exam_set 같은 "역관계"를 가정했으나,
        # 현재 계약은 Session.exam = ForeignKey(Exam)
        # -> Session은 단일 exam만 가진다.
        # =====================================================
        exam_id = getattr(session, "exam_id", None)  # ✅ PATCH
        if not exam_id:
            return EMPTY_SUMMARY

        exam_id = int(exam_id)
        exam_ids = [exam_id]  # ✅ PATCH: 하위 로직(Attempt 통계) 호환용으로 리스트 유지

        # =====================================================
        # ⚠️ PATCH #3 (정의 명확화):
        # participant_count 기준을 "세션 참여자(Progress)"로 통일
        #
        # 이유:
        # - Result는 '시험 제출자'만 잡힘 (미응시/결석/영상만 시청 등 누락 가능)
        # - 운영용 세션 통계라면 SessionProgress가 참여자 모수로 더 안전
        #
        # 만약 "시험 참여자 통계"만 원하면 여기만 Result.count()로 바꾸면 됨.
        # =====================================================
        progresses = SessionProgress.objects.filter(session=session)  # ✅ PATCH (앞에서 재사용)
        participant_count = progresses.count()  # ✅ PATCH

        # ---------------------------------------------
        # 2️⃣ Result 기반 점수 통계 (대표 attempt)
        # ---------------------------------------------
        # ✅ PATCH: Session은 단일 exam이므로 target_id=exam_id로 고정
        results = Result.objects.filter(
            target_type="exam",
            target_id=exam_id,
        )

        agg = results.aggregate(
            avg_score=Avg("total_score"),
            min_score=Min("total_score"),
            max_score=Max("total_score"),
        )

        # =====================================================
        # 🔥 CRITICAL PATCH #2:
        # SessionProgress 필드명 불일치 수정
        #
        # 기존:
        # - failed / clinic_required 를 참조했으나 계약상 존재하지 않음
        #
        # 정답(권장):
        # - pass 기준: completed=True
        # - clinic 기준: ClinicLink (자동 트리거) distinct enrollment
        # =====================================================
        pass_count = progresses.filter(completed=True).count()  # ✅ PATCH

        clinic_count = (
            ClinicLink.objects.filter(
                session=session,
                is_auto=True,
            )
            .values("enrollment_id")
            .distinct()
            .count()
        )  # ✅ PATCH

        pass_rate = (pass_count / participant_count) if participant_count else 0.0
        clinic_rate = (clinic_count / participant_count) if participant_count else 0.0

        # ---------------------------------------------
        # 4️⃣ Attempt 통계 (재시험 비율)
        # ---------------------------------------------
        # =====================================================
        # ✅ PATCH #4 (주석 보강 + 관계 명확화):
        # Session은 단일 exam(FK)만 가지므로 attempt 통계는 exam 단위로 계산한다.
        # =====================================================
        attempts = ExamAttempt.objects.filter(exam_id__in=exam_ids)

        per_enrollment = (
            attempts.values("enrollment_id")
            .annotate(cnt=Count("id"))
        )

        total_attempts = sum(r["cnt"] for r in per_enrollment)
        retake_users = sum(1 for r in per_enrollment if r["cnt"] > 1)

        avg_attempts = (total_attempts / participant_count) if participant_count else 0.0
        retake_ratio = (retake_users / participant_count) if participant_count else 0.0

        return {
            "participant_count": int(participant_count),
            "avg_score": float(agg["avg_score"] or 0.0),
            "min_score": float(agg["min_score"] or 0.0),
            "max_score": float(agg["max_score"] or 0.0),
            "pass_rate": round(float(pass_rate), 4),
            "clinic_rate": round(float(clinic_rate), 4),
            "attempt_stats": {
                "avg_attempts": round(float(avg_attempts), 2),
                "retake_ratio": round(float(retake_ratio), 4),
            },
        }
