from __future__ import annotations

from django.core.files.base import ContentFile
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.assets.omr import constants as C
from apps.domains.assets.omr.services.pdf_generator import (
    LogoValidationError,
    generate_objective_pdf,
)

from apps.domains.exams.models import Exam, ExamAsset


class ObjectiveOMRSaveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        exam_id_raw = request.data.get("exam_id")
        qc_raw = request.data.get("question_count")

        if exam_id_raw is None:
            return Response({"exam_id": "required"}, status=400)
        if qc_raw is None:
            return Response({"question_count": "required"}, status=400)

        try:
            exam_id = int(str(exam_id_raw).strip())
        except Exception:
            return Response({"exam_id": "must be an integer"}, status=400)

        try:
            question_count = int(str(qc_raw).strip())
        except Exception:
            return Response({"question_count": "must be an integer (1~45)"}, status=400)

        if question_count not in C.ALLOWED_QUESTION_COUNTS:
            return Response({"question_count": "must be between 1 and 45"}, status=400)

        try:
            exam = Exam.objects.get(id=exam_id)
        except Exam.DoesNotExist:
            return Response({"exam_id": "not found"}, status=404)

        logo = request.FILES.get("logo")
        if logo is not None:
            ctype = getattr(logo, "content_type", "") or ""
            if ctype and ctype not in C.ALLOWED_LOGO_CONTENT_TYPES:
                return Response({"logo": f"unsupported content_type: {ctype}"}, status=415)

        try:
            pdf_bytes = generate_objective_pdf(question_count=question_count, logo_file=logo)
        except LogoValidationError as e:
            return Response({"logo": str(e)}, status=400)

        filename = f"omr_objective_v2_{question_count}.pdf"

        asset = ExamAsset.objects.create(
            exam=exam,
            asset_type="OMR_TEMPLATE",
            meta={
                "version": "objective_v2_45",
                "question_count": int(question_count),
                "has_logo": bool(logo is not None),
            },
        )

        asset.file.save(filename, ContentFile(pdf_bytes), save=True)

        return Response(
            {"asset_id": asset.id, "asset_type": asset.asset_type, "exam_id": exam.id},
            status=201,
        )
