# PATH: apps/domains/submissions/views/exam_omr_batch_upload_view.py
from __future__ import annotations

from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.core.permissions import TenantResolvedAndStaff
from apps.domains.submissions.models import Submission
from apps.domains.submissions.serializers.submission import SubmissionCreateSerializer
from apps.domains.submissions.services.dispatcher import (
    dispatch_submission,
    resolve_omr_sheet_for_exam,
)


class ExamOMRBatchUploadView(APIView):
    permission_classes = [IsAuthenticated, TenantResolvedAndStaff]

    def post(self, request, exam_id: int):
        """
        multipart/form-data:
          - files: File[]  (반복)
          - (optional) sheet_id: number  (payload로 전달)
        """
        tenant = getattr(request, "tenant", None)
        if not tenant:
            return Response({"detail": "Tenant required"}, status=403)

        files = request.FILES.getlist("files") or []
        if not files:
            # 일부 클라이언트는 file 단일 키를 쓰기도 함
            f = request.FILES.get("file")
            if f:
                files = [f]

        if not files:
            return Response({"detail": "files required"}, status=400)

        # 파일 수 제한
        if len(files) > 100:
            return Response(
                {"detail": "한 번에 최대 100개 파일까지 업로드할 수 있습니다."},
                status=400,
            )

        # 파일별 크기·타입 검증
        ALLOWED_CONTENT_TYPES = {
            "image/jpeg", "image/png", "image/tiff", "application/pdf",
        }
        MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

        for f in files:
            if f.size > MAX_FILE_SIZE:
                return Response(
                    {"detail": f"파일 '{f.name}'의 크기가 10MB를 초과합니다."},
                    status=400,
                )
            if f.content_type not in ALLOWED_CONTENT_TYPES:
                return Response(
                    {"detail": f"파일 '{f.name}'의 형식이 허용되지 않습니다. (허용: JPEG, PNG, TIFF, PDF)"},
                    status=400,
                )

        # exam 테넌트 검증 — 크로스 테넌트 시험 제출 방지 (tenant FK 직접 검증)
        from apps.domains.exams.models import Exam
        if not Exam.objects.filter(id=exam_id, tenant=tenant).exists():
            return Response({"detail": "해당 시험을 찾을 수 없습니다."}, status=400)

        sheet_id = request.data.get("sheet_id")
        payload = {}
        if sheet_id:
            try:
                payload["sheet_id"] = int(sheet_id)
            except Exception:
                return Response({"detail": "sheet_id must be integer"}, status=400)

        try:
            sheet = resolve_omr_sheet_for_exam(
                tenant=tenant,
                exam_id=int(exam_id),
                requested_sheet_id=payload.get("sheet_id"),
            )
        except ValueError as e:
            return Response({"detail": str(e)}, status=400)
        payload["sheet_id"] = int(sheet.id)

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
            sub = ser.save(user=request.user, tenant=tenant)
            dispatch_submission(sub)
            created_ids.append(int(sub.id))

        return Response(
            {"created_count": len(created_ids), "submission_ids": created_ids},
            status=201,
        )
