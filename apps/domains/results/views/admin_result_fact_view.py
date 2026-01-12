# PATH: apps/domains/results/views/admin_result_fact_view.py
"""
Admin ResultFact Debug View

GET /results/admin/facts/?exam_id=&enrollment_id=&limit=100

⚠️ 목적:
- 운영/CS/디버깅
- append-only Fact 직접 조회
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.models import ResultFact


class AdminResultFactView(APIView):
    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def get(self, request):
        exam_id = request.query_params.get("exam_id")
        enrollment_id = request.query_params.get("enrollment_id")
        limit = int(request.query_params.get("limit", 100))

        qs = ResultFact.objects.all().order_by("-id")

        if exam_id:
            qs = qs.filter(target_type="exam", target_id=int(exam_id))
        if enrollment_id:
            qs = qs.filter(enrollment_id=int(enrollment_id))

        qs = qs[: min(limit, 500)]

        return Response([
            {
                "id": f.id,
                "exam_id": f.target_id,
                "enrollment_id": f.enrollment_id,
                "attempt_id": f.attempt_id,
                "question_id": f.question_id,
                "answer": f.answer,
                "is_correct": f.is_correct,
                "score": f.score,
                "max_score": f.max_score,
                "meta": f.meta,
                "created_at": f.created_at,
            }
            for f in qs
        ])
