# apps/domains/results/views/admin_session_exams_summary_view.py
from __future__ import annotations

from django.db.models import Avg, Min, Max, Count
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.serializers.session_exams_summary import SessionExamsSummarySerializer

from apps.domains.lectures.models import Session
from apps.domains.progress.models import SessionProgress, ClinicLink, ProgressPolicy

# ✅ 단일 진실 유틸
from apps.domains.results.utils.session_exam import get_exams_for_session
from apps.domains.results.utils.result_queries import latest_results_per_enrollment


class AdminSessionExamsSummaryView(APIView):
    """
    ✅ Session 기준 시험 요약 API (1 Session : N Exams)

    GET /results/admin/sessions/{session_id}/exams/summary/

    단일 진실 규칙:
    - 세션 단위 pass_rate: SessionProgress.exam_passed 기반 (집계 결과)
    - 세션 단위 clinic_rate: ClinicLink(is_auto=True) enrollment distinct 기반
    - 시험 단위 점수 통계: Result(단, enrollment 중복 방어)
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, session_id: int):
        session = Session.objects.filter(id=int(session_id)).select_related("lecture").first()
        if not session:
            return Response(
                SessionExamsSummarySerializer({
                    "session_id": int(session_id),
                    "participant_count": 0,
                    "pass_rate": 0.0,
                    "clinic_rate": 0.0,
                    "strategy": "MAX",
                    "pass_source": "EXAM",
                    "exams": [],
                }).data
            )

        # 정책(표시용)
        policy = ProgressPolicy.objects.filter(lecture=session.lecture).first()
        strategy = str(getattr(policy, "exam_aggregate_strategy", "MAX"))
        pass_source = str(getattr(policy, "exam_pass_source", "EXAM"))

        # ✅ 세션에 연결된 exams (단일 진실)
        exams = list(get_exams_for_session(session))
        exam_ids = [int(e.id) for e in exams]

        # -----------------------------
        # session-level participant/pass/clinic
        # -----------------------------
        sp_qs = SessionProgress.objects.filter(session=session)
        participant_count = sp_qs.count()

        # 세션 단위 시험 통과율(집계 결과)
        pass_count = sp_qs.filter(exam_passed=True).count()
        pass_rate = (pass_count / participant_count) if participant_count else 0.0

        # clinic_rate(단일 규칙)
        clinic_count = (
            ClinicLink.objects.filter(session=session, is_auto=True)
            .values("enrollment_id").distinct().count()
        )
        clinic_rate = (clinic_count / participant_count) if participant_count else 0.0

        # -----------------------------
        # exam-level stats (Result 기반, enrollment 중복 방어)
        # -----------------------------
        exam_rows = []
        for ex in exams:
            rs = latest_results_per_enrollment(
                target_type="exam",
                target_id=int(ex.id),
            )

            agg = rs.aggregate(
                participant_count=Count("id"),  # 이미 enrollment 1개씩으로 줄였으니 count(id)=participant
                avg_score=Avg("total_score"),
                min_score=Min("total_score"),
                max_score=Max("total_score"),
            )

            pass_score = float(getattr(ex, "pass_score", 0.0) or 0.0)

            pcount = rs.filter(total_score__gte=pass_score).count()
            fcount = rs.filter(total_score__lt=pass_score).count()

            p_total = int(agg["participant_count"] or 0)
            p_rate = (pcount / p_total) if p_total else 0.0

            exam_rows.append({
                "exam_id": int(ex.id),
                "title": str(getattr(ex, "title", "") or ""),
                "pass_score": float(pass_score),

                "participant_count": p_total,
                "avg_score": float(agg["avg_score"] or 0.0),
                "min_score": float(agg["min_score"] or 0.0),
                "max_score": float(agg["max_score"] or 0.0),

                "pass_count": int(pcount),
                "fail_count": int(fcount),
                "pass_rate": round(float(p_rate), 4),
            })

        payload = {
            "session_id": int(session.id),
            "participant_count": int(participant_count),

            # ✅ 의미 고정:
            # pass_rate = SessionProgress.exam_passed 기반 (집계 결과)
            "pass_rate": round(float(pass_rate), 4),

            # ✅ 의미 고정:
            # clinic_rate = ClinicLink(is_auto=True) 기준
            "clinic_rate": round(float(clinic_rate), 4),

            "strategy": strategy,
            "pass_source": pass_source,
            "exams": exam_rows,

            # (권장) pass_rate_source 같은 메타를 serializer에 추가하면 사고 방지에 큰 도움
            # "pass_rate_source": "SESSION_PROGRESS",
            # "clinic_rate_source": "CLINIC_LINK_AUTO",
        }

        return Response(SessionExamsSummarySerializer(payload).data)
