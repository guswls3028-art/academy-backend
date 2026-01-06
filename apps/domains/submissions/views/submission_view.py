from rest_framework.viewsets import ModelViewSet
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from apps.domains.submissions.models import Submission
from apps.domains.submissions.serializers.submission import (
    SubmissionSerializer,
    SubmissionCreateSerializer,
)
from apps.domains.submissions.services.dispatcher import dispatch_submission


class SubmissionViewSet(ModelViewSet):
    """
    Submission 단일 진실 엔드포인트

    - 시험 / 과제 / 영상 업로드 전부 여기서 생성
    - 실제 처리 로직은 Celery Worker가 담당
    """

    queryset = Submission.objects.all().order_by("-id")
    permission_classes = [IsAuthenticated]

    # --------------------------------------------
    # serializer 분기
    # --------------------------------------------
    def get_serializer_class(self):
        # 생성 계열은 CreateSerializer 사용
        if self.action in ("create", "admin_omr_upload"):
            return SubmissionCreateSerializer
        return SubmissionSerializer

    # --------------------------------------------
    # 공통 create (학생/관리자 공용)
    # POST /api/v1/submissions/
    # --------------------------------------------
    def perform_create(self, serializer):
        """
        Submission 생성 직후:
        - status = SUBMITTED
        - dispatcher를 통해 Worker로 전달
        """
        submission = serializer.save(user=self.request.user)
        dispatch_submission(submission)

    # ============================================================
    # 🔥 관리자 OMR 업로드 전용 API
    # POST /api/v1/submissions/admin/omr-upload/
    # ============================================================
    @action(
        detail=False,
        methods=["post"],
        url_path="admin/omr-upload",
    )
    def admin_omr_upload(self, request):
        """
        관리자 OMR 스캔 업로드

        form-data:
        - enrollment_id
        - target_id        (exam_id)
        - file             (pdf / image)

        ⚠️ target_type, source는 서버에서 강제
        """

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

        # 🔥 STEP 2: AI / OMR Worker 디스패치
        dispatch_submission(submission)

        return Response(
            {
                "submission_id": submission.id,
                "status": submission.status,
            },
            status=status.HTTP_201_CREATED,
        )

    # ============================================================
    # 🔁 Submission 재처리
    # POST /api/v1/submissions/{id}/retry/
    # ============================================================
    @action(detail=True, methods=["post"])
    def retry(self, request, pk=None):
        """
        FAILED 상태 Submission 재처리 전용

        - status 리셋
        - 다시 dispatcher 호출
        """

        submission = self.get_object()

        # ❗ 실패한 것만 재처리 허용
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
