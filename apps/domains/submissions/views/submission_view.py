from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from django.utils import timezone

from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.submissions.serializers.submission import (
    SubmissionSerializer,
    SubmissionCreateSerializer,
)
from apps.domains.submissions.services.dispatcher import dispatch_submission
from apps.domains.results.tasks.grading_tasks import grade_submission_task


class SubmissionViewSet(ModelViewSet):

    queryset = Submission.objects.all().order_by("-id")
    permission_classes = [IsAuthenticated]

    def get_serializer_class(self):
        if self.action in ("create", "admin_omr_upload"):
            return SubmissionCreateSerializer
        return SubmissionSerializer

    def perform_create(self, serializer):
        submission = serializer.save(user=self.request.user)
        dispatch_submission(submission)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):

        submission = self.get_object()

        if submission.status != Submission.Status.FAILED:
            return Response(
                {"detail": "Only FAILED submissions can be retried."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        submission.status = Submission.Status.SUBMITTED
        submission.error_message = ""
        submission.save(update_fields=["status", "error_message"])

        dispatch_submission(submission)

        return Response(
            {
                "submission_id": submission.id,
                "status": submission.status,
            }
        )

    # ============================================================
    # ✅ 수동 수정 + 재채점
    # ============================================================
    @action(detail=True, methods=["post"], url_path="manual-edit")
    def manual_edit(self, request, pk=None):

        submission: Submission = self.get_object()

        if submission.status == Submission.Status.GRADING:
            return Response({"detail": "Submission is grading now."}, status=409)

        identifier = request.data.get("identifier")
        answers = request.data.get("answers") or []
        note = str(request.data.get("note") or "manual_edit")

        updated = 0

        for a in answers:
            if not isinstance(a, dict):
                continue

            eqid = a.get("exam_question_id")
            if not eqid:
                continue

            ans = str(a.get("answer") or "")

            SubmissionAnswer.objects.update_or_create(
                submission=submission,
                exam_question_id=int(eqid),
                defaults={"answer": ans},
            )
            updated += 1

        meta = dict(submission.meta or {})
        meta.setdefault("omr", {})
        meta["omr"]["identifier_override"] = identifier

        meta.setdefault("manual_edits", [])
        meta["manual_edits"].append(
            {
                "at": timezone.now().isoformat(),
                "by_user_id": getattr(request.user, "id", None),
                "note": note,
                "updated_answers_count": updated,
                "identifier": identifier,
            }
        )

        meta.setdefault("manual_review", {})
        meta["manual_review"]["required"] = False
        meta["manual_review"]["resolved_at"] = timezone.now().isoformat()

        submission.meta = meta
        submission.status = Submission.Status.ANSWERS_READY
        submission.error_message = ""

        submission.save(update_fields=["meta", "status", "error_message", "updated_at"])

        grade_submission_task.delay(int(submission.id))

        return Response(
            {
                "submission_id": submission.id,
                "status": submission.status,
                "updated": updated,
            }
        )
