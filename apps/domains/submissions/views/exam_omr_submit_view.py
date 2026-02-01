from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.dispatcher import dispatch_submission


class ExamOMRSubmitView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, exam_id: int):
        enrollment_id = request.data.get("enrollment_id")
        sheet_id = request.data.get("sheet_id")
        file_key = request.data.get("file_key")

        if not all([enrollment_id, sheet_id, file_key]):
            return Response(
                {"detail": "enrollment_id, sheet_id, file_key required"}, status=400
            )

        submission = Submission.objects.create(
            user=request.user,
            enrollment_id=int(enrollment_id),
            target_type=Submission.TargetType.EXAM,
            target_id=int(exam_id),
            source=Submission.Source.OMR_SCAN,
            file_key=str(file_key),
            payload={"sheet_id": int(sheet_id)},
        )

        dispatch_submission(submission)

        return Response(
            {"submission_id": submission.id, "status": submission.status}, status=201
        )
