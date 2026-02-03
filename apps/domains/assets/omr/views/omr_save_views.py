from django.core.files.base import ContentFile
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from apps.domains.exams.models import Exam, ExamAsset
from apps.domains.assets.omr.services.pdf_generator import generate_objective_pdf
from apps.domains.assets.omr.services.meta_generator import build_objective_template_meta

class ObjectiveOMRSaveView(APIView):
    """
    생성된 PDF와 좌표 Meta 데이터를 ExamAsset 모델에 실제 저장합니다.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        exam_id = request.data.get("exam_id")
        qc_raw = request.data.get("question_count")
        logo = request.FILES.get("logo")

        try:
            exam = Exam.objects.get(id=exam_id)
            question_count = int(qc_raw)
        except (Exam.DoesNotExist, TypeError, ValueError):
            return Response({"error": "valid exam_id and question_count(1~45) required"}, status=400)

        # 1. PDF 생성
        pdf_bytes = generate_objective_pdf(
            question_count=question_count,
            logo_file=logo,
            exam_title=exam.title,
            subject_round=f"{exam.subject} ({exam.round}회)"
        )

        # 2. OCR용 Meta 생성
        meta_data = build_objective_template_meta(question_count=question_count)
        meta_data["has_logo"] = bool(logo)

        # 3. Asset 레코드 생성 및 파일 저장
        asset = ExamAsset.objects.create(
            exam=exam,
            asset_type="OMR_TEMPLATE",
            meta=meta_data
        )
        asset.file.save(f"omr_v2_e{exam_id}_q{question_count}.pdf", ContentFile(pdf_bytes))

        return Response({"asset_id": asset.id, "file_url": asset.file.url}, status=201)