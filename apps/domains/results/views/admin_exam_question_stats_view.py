# PATH: apps/domains/results/views/admin_exam_question_stats_view.py
"""
Admin / Teacher Exam Question Statistics

- ResultFact 기반 (append-only)
- support.analytics 완전 대체
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from django.db.models import Count

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import ResultFact


class AdminExamQuestionStatsView(APIView):
    """
    GET /results/admin/exams/<exam_id>/questions/
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        facts = ResultFact.objects.filter(
            target_type="exam",
            target_id=int(exam_id),
        )

        rows = []

        for qid in facts.values_list("question_id", flat=True).distinct():
            qf = facts.filter(question_id=qid)
            total = qf.count()
            correct = qf.filter(is_correct=True).count()

            rows.append({
                "question_id": qid,
                "attempts": total,
                "correct_count": correct,
                "wrong_count": total - correct,
                "correct_rate": (correct / total) if total else 0.0,
            })

        return Response(rows)
