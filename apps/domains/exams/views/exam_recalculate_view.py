from __future__ import annotations

from django.db import transaction
from django.shortcuts import get_object_or_404

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.exams.models import Exam
from apps.domains.results.permissions import IsTeacherOrAdmin
from apps.domains.results.services.grading_service import grade_submission
from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.transition import (
    InvalidTransitionError,
    transit_save,
)


class ExamRecalculateView(APIView):
    """
    POST /api/v1/exams/<exam_id>/recalculate/

    Re-grade completed/ready submissions after answer-key or score-setting changes.
    The frontend has exposed this action for admins/teachers, so the API must be
    explicit and tenant-scoped instead of falling through to 404.
    """

    permission_classes = [IsAuthenticated, TenantResolvedAndMember, IsTeacherOrAdmin]
    REGRADABLE_STATUSES = {
        Submission.Status.DONE,
        Submission.Status.ANSWERS_READY,
    }

    def post(self, request, exam_id: int):
        tenant = getattr(request, "tenant", None)
        exam = get_object_or_404(
            Exam.objects.filter(tenant=tenant),
            id=int(exam_id),
        )

        submissions = list(
            Submission.objects.filter(
                tenant=tenant,
                target_type=Submission.TargetType.EXAM,
                target_id=int(exam.id),
            )
            .exclude(status=Submission.Status.SUPERSEDED)
            .order_by("id")
            .values_list("id", "status")
        )

        graded = 0
        skipped = 0
        failed: list[dict[str, object]] = []

        for submission_id, current_status in submissions:
            if current_status not in self.REGRADABLE_STATUSES:
                skipped += 1
                continue
            try:
                self._prepare_for_regrade(submission_id, current_status)
                grade_submission(int(submission_id))
                graded += 1
            except Exception as exc:
                failed.append(
                    {
                        "submission_id": int(submission_id),
                        "status": str(current_status),
                        "detail": str(exc) or exc.__class__.__name__,
                    }
                )

        return Response(
            {
                "exam_id": int(exam.id),
                "total": len(submissions),
                "graded": graded,
                "skipped": skipped,
                "failed": failed,
            }
        )

    @staticmethod
    @transaction.atomic
    def _prepare_for_regrade(submission_id: int, current_status: str) -> None:
        if current_status == Submission.Status.ANSWERS_READY:
            return

        submission = Submission.objects.select_for_update().get(id=int(submission_id))
        if submission.status == Submission.Status.ANSWERS_READY:
            return

        try:
            transit_save(
                submission,
                Submission.Status.ANSWERS_READY,
                admin_override=True,
                actor="ExamRecalculateView",
            )
        except InvalidTransitionError:
            # Let the caller record this submission as failed while continuing the batch.
            raise
