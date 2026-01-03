# apps/domains/results/views/wrong_note_view.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from django.db.models import F

from apps.domains.results.models import ResultFact
from apps.domains.exams.models import Exam
from apps.domains.lectures.models import Session


class WrongNoteView(APIView):
    """
    오답노트 조회 API (v1)
    - ResultFact 기반
    - append-only
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        """
        Query Params
        - enrollment_id (required)
        - lecture_id (optional)
        - exam_id (optional)
        - from_session_order (optional, default=2)
        """

        enrollment_id = request.query_params.get("enrollment_id")
        if not enrollment_id:
            return Response(
                {"detail": "enrollment_id is required"},
                status=400,
            )

        lecture_id = request.query_params.get("lecture_id")
        exam_id = request.query_params.get("exam_id")
        from_order = int(request.query_params.get("from_session_order", 2))

        qs = ResultFact.objects.filter(
            enrollment_id=enrollment_id,
            is_correct=False,
            target_type="exam",
        )

        # -----------------------------
        # 시험 기준 필터
        # -----------------------------
        if exam_id:
            qs = qs.filter(target_id=exam_id)

        # -----------------------------
        # 강의 / 주차 기준 필터
        # -----------------------------
        if lecture_id:
            exam_ids = (
                Exam.objects
                .filter(
                    lecture_id=lecture_id,
                    session__order__gte=from_order,
                )
                .values_list("id", flat=True)
            )
            qs = qs.filter(target_id__in=exam_ids)

        qs = qs.select_related(None).order_by(
            "target_id",
            "question_id",
        )

        result = []

        for f in qs:
            result.append({
                "exam_id": f.target_id,
                "question_id": f.question_id,
                "answer": f.answer,
                "score": f.score,
                "max_score": f.max_score,
                "source": f.source,
                "meta": f.meta,
                "created_at": f.created_at,
            })

        return Response({
            "enrollment_id": int(enrollment_id),
            "count": len(result),
            "items": result,
        })
