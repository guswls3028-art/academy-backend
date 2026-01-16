# apps/domains/results/views/admin_exam_summary_view.py
from __future__ import annotations

from django.db.models import Avg, Min, Max

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.serializers.admin_exam_summary import AdminExamSummarySerializer
from apps.domains.exams.models import Exam
from apps.domains.progress.models import ClinicLink

# ✅ 단일 진실 유틸
from apps.domains.results.utils.session_exam import get_primary_session_for_exam
from apps.domains.results.utils.result_queries import latest_results_per_enrollment


class AdminExamSummaryView(APIView):
    """
    LEGACY COMPAT
    GET /results/admin/exams/<exam_id>/summary/

    ✅ 계약 유지(프론트 안정성):
    - participant_count, avg/min/max, pass_count/fail_count/pass_rate, clinic_count

    ✅ 정합성 강화:
    - Result 중복 enrollment 방어: 최신 Result만 집계
    - clinic_count 기준 통일: ClinicLink(is_auto=True) enrollment distinct
    - Session↔Exam 매핑 단일화(utils.session_exam)
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        exam_id = int(exam_id)

        EMPTY = {
            "participant_count": 0,
            "avg_score": 0.0,
            "min_score": 0.0,
            "max_score": 0.0,
            "pass_count": 0,
            "fail_count": 0,
            "pass_rate": 0.0,
            "clinic_count": 0,
        }

        exam = Exam.objects.filter(id=exam_id).first()
        pass_score = float(getattr(exam, "pass_score", 0.0) or 0.0) if exam else 0.0

        # ✅ 중복 방어: enrollment당 최신 Result만
        rs = latest_results_per_enrollment(target_type="exam", target_id=exam_id)

        participant_count = rs.values("enrollment_id").distinct().count()
        if participant_count == 0:
            return Response(AdminExamSummarySerializer(EMPTY).data)

        agg = rs.aggregate(
            avg_score=Avg("total_score"),
            min_score=Min("total_score"),
            max_score=Max("total_score"),
        )

        pass_count = rs.filter(total_score__gte=pass_score).count()
        fail_count = rs.filter(total_score__lt=pass_score).count()
        pass_rate = (pass_count / participant_count) if participant_count else 0.0

        # ✅ clinic_count는 session 기반으로만 계산 가능(시험만으론 clinic이 정의되지 않음)
        clinic_count = 0
        session = get_primary_session_for_exam(exam_id)
        if session:
            clinic_count = (
                ClinicLink.objects.filter(session=session, is_auto=True)
                .values("enrollment_id")
                .distinct()
                .count()
            )

        payload = {
            "participant_count": int(participant_count),
            "avg_score": float(agg["avg_score"] or 0.0),
            "min_score": float(agg["min_score"] or 0.0),
            "max_score": float(agg["max_score"] or 0.0),
            "pass_count": int(pass_count),
            "fail_count": int(fail_count),
            "pass_rate": round(float(pass_rate), 4),
            "clinic_count": int(clinic_count),
        }

        return Response(AdminExamSummarySerializer(payload).data)
