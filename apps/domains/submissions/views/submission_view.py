# PATH: apps/domains/submissions/views/submission_view.py
from __future__ import annotations

from django.db import transaction
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
from apps.domains.submissions.services.transition import (
    transit_save,
    InvalidTransitionError,
)
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
        # target_id(exam/homework)가 해당 테넌트 소속인지 검증
        target_type = serializer.validated_data.get("target_type")
        target_id = serializer.validated_data.get("target_id")
        if target_type and target_id:
            if not self._validate_target_tenant(target_type, target_id, tenant):
                from rest_framework.exceptions import PermissionDenied
                raise PermissionDenied("대상이 해당 학원에 속하지 않습니다.")
        # enrollment_id 소유권 검증: 학생은 자신의 enrollment만 사용 가능
        enrollment_id = serializer.validated_data.get("enrollment_id")
        if enrollment_id:
            student = getattr(self.request.user, "student_profile", None)
            if student:
                from apps.domains.enrollment.models import Enrollment
                if not Enrollment.objects.filter(
                    id=enrollment_id, student=student, tenant=tenant,
                ).exists():
                    from rest_framework.exceptions import PermissionDenied
                    raise PermissionDenied("해당 수강 정보에 접근할 수 없습니다.")
        submission = serializer.save(user=self.request.user, tenant=tenant)
        dispatch_submission(submission)

    @staticmethod
    def _validate_target_tenant(target_type, target_id, tenant) -> bool:
        """target_id가 해당 tenant 소속인지 검증."""
        try:
            if target_type == Submission.TargetType.EXAM:
                from apps.domains.exams.models import Exam
                return Exam.objects.filter(
                    id=int(target_id), sessions__lecture__tenant=tenant,
                ).exists()
            elif target_type == Submission.TargetType.HOMEWORK:
                from apps.domains.homework.models import HomeworkPolicy
                return HomeworkPolicy.objects.filter(
                    id=int(target_id), tenant=tenant,
                ).exists()
        except Exception:
            pass
        return False  # 알 수 없는 target_type은 거부 (fail-closed)

    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        with transaction.atomic():
            submission = Submission.objects.select_for_update().get(pk=self.get_object().pk)

            try:
                transit_save(
                    submission, Submission.Status.SUBMITTED,
                    actor="admin.retry",
                )
            except InvalidTransitionError:
                return Response(
                    {"detail": "Only FAILED submissions can be retried."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        dispatch_submission(submission)

        return Response(
            {
                "submission_id": submission.id,
                "status": submission.status,
            }
        )

    @action(detail=True, methods=["get", "post"], url_path="manual-edit")
    def manual_edit(self, request, pk=None):
        if request.method == "GET":
            return self._manual_edit_get(request, pk)
        return self._manual_edit_post(request, pk)

    def _manual_edit_get(self, request, pk=None):
        """GET: 현재 답안 목록 + identifier 반환 (수동 편집 화면용)."""
        submission: Submission = self.get_object()
        answers_qs = SubmissionAnswer.objects.filter(
            submission=submission,
        ).order_by("exam_question_id")
        answers_data = []
        for a in answers_qs:
            answers_data.append({
                "question_id": a.exam_question_id,
                "question_no": a.exam_question_id,
                "answer": a.answer or "",
            })
        meta = dict(submission.meta or {})
        identifier = None
        omr = meta.get("omr") or {}
        if isinstance(omr, dict):
            identifier = omr.get("identifier_override") or omr.get("identifier")
        return Response({
            "identifier": identifier,
            "answers": answers_data,
            "meta": {
                "manual_review": meta.get("manual_review"),
                "ai_result": meta.get("ai_result"),
            },
        })

    @transaction.atomic
    def _manual_edit_post(self, request, pk=None):
        submission: Submission = Submission.objects.select_for_update().get(pk=self.get_object().pk)

        # admin_override=True: DONE/FAILED/SUBMITTED/DISPATCHED/NI → ANSWERS_READY 허용
        # GRADING/SUPERSEDED → ANSWERS_READY 차단 (transition.py에서 강제)
        try:
            transit_save(
                submission, Submission.Status.ANSWERS_READY,
                admin_override=True,
                actor=f"admin.manual_edit.user_{getattr(request.user, 'id', '?')}",
            )
        except InvalidTransitionError as e:
            return Response({"detail": str(e)}, status=409)

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
        submission.save(update_fields=["meta", "updated_at"])

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
