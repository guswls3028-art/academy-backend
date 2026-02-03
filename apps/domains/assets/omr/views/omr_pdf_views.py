from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.assets.omr import constants as C
from apps.domains.assets.omr.services.pdf_generator import generate_objective_pdf
from apps.domains.assets.omr.services.meta_generator import build_objective_template_meta

class ObjectiveOMRPdfView(APIView):
    """
    사용자가 선택한 옵션으로 PDF 미리보기를 생성합니다.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        qc_raw = request.data.get("question_count")
        exam_title = request.data.get("exam_title", "Custom Exam")
        subject_round = request.data.get("subject_round", "1st Round")
        logo = request.FILES.get("logo")

        try:
            question_count = int(qc_raw)
            if question_count not in C.ALLOWED_QUESTION_COUNTS:
                raise ValueError
        except (TypeError, ValueError):
            return Response({"error": "question_count must be 1~45"}, status=400)

        pdf_bytes = generate_objective_pdf(
            question_count=question_count,
            logo_file=logo,
            exam_title=exam_title,
            subject_round=subject_round
        )

        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="omr_v2_{question_count}.pdf"'
        return response

class ObjectiveOMRMetaView(APIView):
    """
    OCR AI 워커가 읽어야 할 각 버블의 좌표(mm) 데이터를 반환합니다.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qc_raw = request.query_params.get("question_count")
        try:
            question_count = int(qc_raw)
            meta = build_objective_template_meta(question_count=question_count)
            return Response(meta)
        except Exception:
            return Response({"error": "invalid question_count"}, status=400)