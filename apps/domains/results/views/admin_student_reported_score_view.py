from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.support.results.student_reported_scores import (
    review_student_score,
    serialize_reported_score,
)


class AdminStudentReportedScoreReviewView(APIView):
    """PATCH /results/admin/reported-scores/:id/review/ — verify or reject."""

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def patch(self, request, score_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant not resolved"}, status=403)

        action = str(request.data.get("action") or "").strip()
        note = str(request.data.get("review_note") or "").strip()
        try:
            row = review_student_score(
                tenant=tenant,
                score_id=score_id,
                action=action,
                reviewed_by=request.user,
                review_note=note,
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        if not row:
            return Response({"detail": "reported score not found"}, status=404)
        return Response(serialize_reported_score(row))
