# PATH: apps/domains/assets/omr/views/omr_save_views.py
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
    """
    POST /api/v1/assets/omr/objective/save/

    multipart/form-data:
      - exam_id: int (required)
      - question_count: 10 | 20 | 30 (required)
      - logo: optional image

    response (201):
      {
        "asset_id": number,
        "asset_type": "OMR_TEMPLATE",
        "exam_id": number
      }

    책임:
    - assets 도메인 범위 내에서
      OMR 시험지(PDF)를 생성하고 "양식 자산"으로 저장
    - 채점/제출/결과/AI 호출 절대 없음
    """
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
            return Response({"question_count": "must be one of 10, 20, 30"}, status=400)

        if question_count not in C.ALLOWED_QUESTION_COUNTS:
            return Response({"question_count": "must be one of 10, 20, 30"}, status=400)

        try:
            exam = Exam.objects.get(id=exam_id)
        except Exam.DoesNotExist:
            return Response({"exam_id": "not found"}, status=404)

        logo = request.FILES.get("logo")
        if logo is not None:
            ctype = getattr(logo, "content_type", "") or ""
            if ctype and ctype not in C.ALLOWED_LOGO_CONTENT_TYPES:
                return Response(
                    {"logo": f"unsupported content_type: {ctype}"},
                    status=415,
                )

        try:
            pdf_bytes = generate_objective_pdf(
                question_count=question_count,
                logo_file=logo,
            )
        except LogoValidationError as e:
            return Response({"logo": str(e)}, status=400)
        except ValueError:
            return Response({"question_count": "must be one of 10, 20, 30"}, status=400)

        filename = f"omr_objective_v1_{question_count}.pdf"

        asset = ExamAsset.objects.create(
            exam=exam,
            asset_type="OMR_TEMPLATE",
            meta={
                "version": "objective_v1",
                "question_count": int(question_count),
                "has_logo": bool(logo is not None),
            },
        )

        asset.file.save(
            filename,
            ContentFile(pdf_bytes),
            save=True,
        )

        return Response(
            {
                "asset_id": asset.id,
                "asset_type": asset.asset_type,
                "exam_id": exam.id,
            },
            status=201,
        )
