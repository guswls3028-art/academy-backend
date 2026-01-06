from rest_framework.viewsets import ModelViewSet
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status

from apps.domains.submissions.models import Submission
from apps.domains.submissions.serializers.submission import (
    SubmissionSerializer,
    SubmissionCreateSerializer,
)
from apps.domains.submissions.services.dispatcher import dispatch_submission


class SubmissionViewSet(ModelViewSet):
    """
    Submission API

    - 일반 제출: POST /api/v1/submissions/
    - 관리자 OMR 업로드: POST /api/v1/submissions/admin/omr-upload/
    - 실패 재처리: POST /api/v1/submissions/{id}/retry/
    """

    queryset = Submission.objects.all().order_by("-id")
    permission_classes = [IsAuthenticated]

    # ------------------------------------------------------------
    # Serializer 선택
    # ------------------------------------------------------------
    def get_serializer_class(self):
        if self.action in ("create", "admin_omr_upload"):
            return SubmissionCreateSerializer
        return SubmissionSerializer

    # ------------------------------------------------------------
    # 기본 생성 (ONLINE / 일반 제출)
    # ------------------------------------------------------------
    def perform_create(self, serializer):
        submission = serializer.save(user=self.request.user)
        # 🔥 생성 직후 단일 진입점 디스패치
        dispatch_submission(submission)

    # ============================================================
    # 🔥 관리자 OMR 업로드 전용 API
    #
    # POST /api/v1/submissions/admin/omr-upload/
    #
    # form-data:
    # - enrollment_id
    # - target_id (exam_id)
    # - file (pdf / image)
    # ============================================================
    @action(
        detail=False,
        methods=["post"],
        url_path="admin/omr-upload",
    )
    def admin_omr_upload(self, request):
        serializer = SubmissionCreateSerializer(
            data={
                "enrollment_id": request.data.get("enrollment_id"),
                "target_type": Submission.TargetType.EXAM,
                "target_id": request.data.get("target_id"),
                "source": Submission.Source.OMR_SCAN,
                "file": request.FILES.get("file"),
            }
        )
        serializer.is_valid(raise_exception=True)

        submission = serializer.save(user=request.user)

        # 🔥 STEP 2: AI Job 디스패치 (R2 presigned URL 포함)
        dispatch_submission(submission)

        return Response(
            {
                "submission_id": submission.id,
                "status": submission.status,
            },
            status=status.HTTP_201_CREATED,
        )

    # ============================================================
    # 🔁 Submission 재처리 API
    #
    # POST /api/v1/submissions/{id}/retry/
    #
    # - FAILED 상태만 허용
    # - 상태 리셋 후 재디스패치
    # ============================================================
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        submission = self.get_object()

        # ❗ 실패한 것만 재처리 가능
        if submission.status != Submission.Status.FAILED:
            return Response(
                {"detail": "Only FAILED submissions can be retried."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 상태 리셋
        submission.status = Submission.Status.SUBMITTED
        submission.error_message = ""
        submission.save(update_fields=["status", "error_message"])

        # 다시 디스패치
        dispatch_submission(submission)

        return Response(
            {
                "submission_id": submission.id,
                "status": submission.status,
            }
        )
