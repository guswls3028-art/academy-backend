# apps/domains/results/views/wrong_note_pdf_view.py
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.results.models.wrong_note_pdf import WrongNotePDF

# ✅ 네 프로젝트 덤프 기준 실제 파일명은 wrong_note_pdf_tasks.py
# (이거 틀리면 바로 ImportError/CI 터짐)
from apps.domains.results.tasks.wrong_note_pdf_tasks import generate_wrong_note_pdf_task


class WrongNotePDFCreateView(APIView):
    """
    오답노트 PDF 생성 요청

    POST /results/wrong-notes/pdf/

    body:
    - enrollment_id (required)
    - lecture_id (optional)
    - exam_id (optional)
    - from_session_order (optional, default=2)
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        enrollment_id = request.data.get("enrollment_id")
        lecture_id = request.data.get("lecture_id")
        exam_id = request.data.get("exam_id")
        from_order = request.data.get("from_session_order", 2)

        if not enrollment_id:
            return Response({"detail": "enrollment_id required"}, status=400)

        job = WrongNotePDF.objects.create(
            enrollment_id=enrollment_id,
            lecture_id=lecture_id,
            exam_id=exam_id,
            from_session_order=from_order,
        )

        # ✅ celery task 실행
        generate_wrong_note_pdf_task.delay(job.id)

        return Response({
            "job_id": job.id,
            "status": job.status,
        })
