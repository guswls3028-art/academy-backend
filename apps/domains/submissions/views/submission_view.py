# PATH: apps/domains/submissions/views/submission_view.py
from __future__ import annotations

from django.utils import timezone

from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.submissions.models import Submission, SubmissionAnswer
from apps.domains.submissions.serializers.submission import (
    SubmissionSerializer,
    SubmissionCreateSerializer,
)
from apps.domains.submissions.services.dispatcher import dispatch_submission
from apps.domains.results.services.grading_service import grade_submission


class SubmissionViewSet(ModelViewSet):
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    def get_queryset(self):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return Submission.objects.none()
        return Submission.objects.filter(tenant=tenant).order_by("-id")

    def get_serializer_class(self):
        if self.action in ("create", "admin_omr_upload"):
            return SubmissionCreateSerializer
        return SubmissionSerializer

    @action(detail=False, methods=["post"], url_path="admin/omr-upload")
    def admin_omr_upload(self, request):
        """
        POST /api/v1/submissions/submissions/admin/omr-upload/
        form-data: enrollment_id, target_id (exam_id), file
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant required"}, status=403)

        enrollment_id = request.data.get("enrollment_id")
        target_id = request.data.get("target_id")
        file_obj = request.FILES.get("file")

        if not target_id:
            return Response({"detail": "target_id (exam_id) required"}, status=400)
        if not file_obj:
            return Response({"detail": "file required"}, status=400)

        try:
            exam_id = int(target_id)
        except (TypeError, ValueError):
            return Response({"detail": "target_id must be an integer"}, status=400)

        payload = {}
        if request.data.get("sheet_id"):
            try:
                payload["sheet_id"] = int(request.data.get("sheet_id"))
            except (TypeError, ValueError):
                pass

        ser = SubmissionCreateSerializer(
            data={
                "enrollment_id": int(enrollment_id) if enrollment_id else None,
                "target_type": Submission.TargetType.EXAM,
                "target_id": exam_id,
                "source": Submission.Source.OMR_SCAN,
                "payload": payload or None,
                "file": file_obj,
            }
        )
        ser.is_valid(raise_exception=True)
        submission = ser.save(user=request.user, tenant=tenant)
        dispatch_submission(submission)

        return Response(
            {"submission_id": submission.id, "status": submission.status},
            status=201,
        )

    def perform_create(self, serializer):
        tenant = getattr(self.request, "tenant", None)
        if not tenant:
            return
        submission = serializer.save(user=self.request.user, tenant=tenant)
        dispatch_submission(submission)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        submission: Submission = self.get_object()

        if submission.status != Submission.Status.FAILED:
            return Response(
                {"detail": "Only FAILED submissions can be retried."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        submission.status = Submission.Status.SUBMITTED
        submission.error_message = ""
        submission.save(update_fields=["status", "error_message", "updated_at"])

        dispatch_submission(submission)

        return Response(
            {
                "submission_id": submission.id,
                "status": submission.status,
            }
        )

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
                defaults={"answer": ans, "tenant": submission.tenant},
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

        try:
            result_obj = grade_submission(int(submission.id))
        except Exception:
            return Response(
                {
                    "submission_id": submission.id,
                    "status": submission.status,
                    "updated": updated,
                    "detail": "grading failed",
                },
                status=500,
            )

        return Response(
            {
                "submission_id": submission.id,
                "status": submission.status,
                "updated": updated,
                "graded": True,
                "result_id": getattr(result_obj, "id", None),
            }
        )
