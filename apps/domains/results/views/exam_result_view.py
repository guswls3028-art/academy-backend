# PATH: apps/domains/results/views/exam_result_view.py
"""
⚠️ LEGACY APIs (DEPRECATED)

- Admin 전용 API는 /admin/* 경로 사용
- 본 파일은 과거 프론트 호환용
- 추후 삭제 예정
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from django.db.models import Avg, Min, Max, StdDev, Count

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import Result, ResultFact
from apps.domains.exams.models import Exam
from apps.domains.progress.models import ProgressPolicy


class ExamStatsView(APIView):
    """
    ⚠️ DEPRECATED
    시험 요약 통계 (Legacy)
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        exam = Exam.objects.select_related("lecture").get(id=exam_id)
        policy = ProgressPolicy.objects.get(lecture=exam.lecture)

        qs = Result.objects.filter(
            target_type="exam",
            target_id=exam_id,
        )

        agg = qs.aggregate(
            avg=Avg("total_score"),
            std=StdDev("total_score"),
            min=Min("total_score"),
            max=Max("total_score"),
            participants=Count("id"),
        )

        participants = agg["participants"] or 0
        passed = qs.filter(
            total_score__gte=policy.exam_pass_score
        ).count()

        pass_rate = (passed / participants) if participants else 0.0

        return Response({
            "exam_id": exam_id,
            "participants": participants,
            "avg": agg["avg"],
            "std": agg["std"],
            "min": agg["min"],
            "max": agg["max"],
            "pass_rate": pass_rate,
        })


class ExamQuestionStatsView(APIView):
    """
    ⚠️ DEPRECATED
    시험 문항별 통계 (Legacy)
    """

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request, exam_id: int):
        facts = ResultFact.objects.filter(
            target_type="exam",
            target_id=exam_id,
        )

        rows = []

        for qid in facts.values_list("question_id", flat=True).distinct():
            qf = facts.filter(question_id=qid)
            total = qf.count()
            correct = qf.filter(is_correct=True).count()

            rows.append({
                "question_id": qid,
                "correct_rate": (correct / total) if total else 0.0,
            })

        return Response(rows)
