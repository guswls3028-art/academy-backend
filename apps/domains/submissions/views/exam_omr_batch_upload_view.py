# PATH: apps/domains/submissions/views/exam_omr_batch_upload_view.py
from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.submissions.models import Submission
from apps.domains.submissions.serializers.submission import SubmissionCreateSerializer
from apps.domains.submissions.services.dispatcher import dispatch_submission


class ExamOMRBatchUploadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, exam_id: int):
        """
        multipart/form-data:
          - files: File[]  (반복)
          - (optional) sheet_id: number  (payload로 전달)
        """
        files = request.FILES.getlist("files") or []
        if not files:
            # 일부 클라이언트는 file 단일 키를 쓰기도 함
            f = request.FILES.get("file")
            if f:
                files = [f]

        if not files:
            return Response({"detail": "files required"}, status=400)

        sheet_id = request.data.get("sheet_id")
        payload = {}
        if sheet_id:
            try:
                payload["sheet_id"] = int(sheet_id)
            except Exception:
                payload["sheet_id"] = sheet_id

        created_ids = []

        for f in files:
            # ✅ OMR_SCAN은 enrollment_id 없이 생성 가능(Serializer 정책과 일치)
            ser = SubmissionCreateSerializer(
                data={
                    "enrollment_id": None,
                    "target_type": Submission.TargetType.EXAM,
                    "target_id": int(exam_id),
                    "source": Submission.Source.OMR_SCAN,
                    "payload": payload or None,
                    "file": f,
                }
            )
            ser.is_valid(raise_exception=True)
            sub = ser.save(user=request.user)
            dispatch_submission(sub)
            created_ids.append(int(sub.id))

        return Response(
            {"created_count": len(created_ids), "submission_ids": created_ids},
            status=201,
        )
