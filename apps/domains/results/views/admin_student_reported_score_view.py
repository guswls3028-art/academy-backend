from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.support.results.student_reported_scores import (
    ReportedScoreTransitionConflict,
    review_student_scores,
    serialize_reported_score,
)


class AdminStudentReportedScoreReviewView(APIView):
    """PATCH /results/admin/reported-scores/:id/review/ — verify, reject or void."""

    permission_classes = [IsAuthenticated, IsTeacherOrAdmin]

    def patch(self, request, score_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "tenant not resolved"}, status=403)

        action = str(request.data.get("action") or "").strip()
        note = str(request.data.get("review_note") or "").strip()
        review_all_evidence = request.data.get("review_all_evidence") is True
        grade_scale_confirmed = request.data.get("grade_scale_confirmed") is True
        try:
            rows = review_student_scores(
                tenant=tenant,
                score_id=score_id,
                action=action,
                reviewed_by=request.user,
                review_note=note,
                review_all_evidence=review_all_evidence,
                grade_scale_confirmed=grade_scale_confirmed,
            )
        except ReportedScoreTransitionConflict as exc:
            return Response({"detail": str(exc)}, status=409)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        if not rows:
            return Response({"detail": "reported score not found"}, status=404)
        serialized = [serialize_reported_score(row) for row in rows]
        if review_all_evidence:
            return Response({"score_submissions": serialized})
        return Response(serialized[0])
