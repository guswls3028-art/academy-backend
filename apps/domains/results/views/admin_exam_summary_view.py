# PATH: apps/domains/results/views/admin_exam_summary_view.py
"""
Admin / Teacher Exam Summary
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from django.db.models import Avg, Min, Max, Count

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import Result
from apps.domains.results.serializers.admin_exam_summary import (
    AdminExamSummarySerializer,
)

from apps.domains.progress.models import ProgressPolicy, SessionProgress
from apps.domains.lectures.models import Session


class AdminExamSummaryView(APIView):
    """
    GET /results/admin/exams/<exam_id>/summary/
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        qs = Result.objects.filter(
            target_type="exam",
            target_id=int(exam_id),
        )

        agg = qs.aggregate(
            participant_count=Count("id"),
            avg_score=Avg("total_score"),
            min_score=Min("total_score"),
            max_score=Max("total_score"),
        )

        session = Session.objects.filter(exam__id=exam_id).first()

        policy = (
            ProgressPolicy.objects
            .filter(lecture=session.lecture)
            .first()
            if session else None
        )

        pass_score = policy.exam_pass_score if policy else 0

        pass_count = qs.filter(total_score__gte=pass_score).count()
        fail_count = qs.filter(total_score__lt=pass_score).count()

        participant_count = agg["participant_count"] or 0
        pass_rate = (
            pass_count / participant_count
            if participant_count else 0.0
        )

        clinic_count = (
            SessionProgress.objects
            .filter(session=session, clinic_required=True)
            .count()
            if session else 0
        )

        return Response(
            AdminExamSummarySerializer({
                "participant_count": participant_count,
                "avg_score": float(agg["avg_score"] or 0.0),
                "min_score": float(agg["min_score"] or 0.0),
                "max_score": float(agg["max_score"] or 0.0),
                "pass_count": pass_count,
                "fail_count": fail_count,
                "pass_rate": round(float(pass_rate), 4),
                "clinic_count": clinic_count,
            }).data
        )
