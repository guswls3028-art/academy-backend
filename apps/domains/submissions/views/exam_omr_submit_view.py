# PATH: apps/domains/submissions/views/exam_omr_submit_view.py
"""
🔥 STEP 2 (정리 완료판)

시험 OMR 전용 Submission 진입점

설계 원칙 (중요):
- ❌ status 직접 제어 금지
- ❌ AIJob 직접 생성 금지
- ✔ Submission 생성만 수행
- ✔ 이후 흐름은 dispatcher 단일 진실
"""

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.dispatcher import dispatch_submission


class ExamOMRSubmitView(APIView):
    """
    POST /api/v1/submissions/exams/<exam_id>/omr/

    body:
    {
        "enrollment_id": 123,
        "sheet_id": 45,
        "file_key": "exams/submissions/abc.jpg"
    }
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, exam_id: int):
        enrollment_id = request.data.get("enrollment_id")
        sheet_id = request.data.get("sheet_id")
        file_key = request.data.get("file_key")

        if not all([enrollment_id, sheet_id, file_key]):
            return Response(
                {"detail": "enrollment_id, sheet_id, file_key are required"},
                status=400,
            )

        # -------------------------------------------------
        # 1️⃣ Submission 생성
        # -------------------------------------------------
        # ⚠️ status는 SUBMITTED 고정
        # ⚠️ 이후 상태 전이는 dispatcher / AI / grader 책임
        submission = Submission.objects.create(
            user=request.user,
            target_type=Submission.TargetType.EXAM,
            target_id=int(exam_id),
            enrollment_id=int(enrollment_id),
            source=Submission.Source.OMR_SCAN,
            file_key=str(file_key),
            payload={
                # OMR 전용 메타는 payload에만 둠
                "sheet_id": int(sheet_id),
            },
        )

        # -------------------------------------------------
        # 2️⃣ 단일 진입점: dispatcher
        # -------------------------------------------------
        dispatch_submission(submission)

        return Response(
            {
                "submission_id": submission.id,
                "status": submission.status,
            },
            status=201,
        )
