from __future__ import annotations

from django.http import HttpResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.domains.assets.omr import constants as C
from apps.domains.assets.omr.services.pdf_generator import (
    generate_objective_pdf,
    LogoValidationError,
)
from apps.domains.assets.omr.services.meta_generator import build_objective_template_meta


class ObjectiveOMRPdfView(APIView):
    """
    POST /api/v1/assets/omr/objective/pdf/
    multipart/form-data:
      - question_count: 1..45 (required)
      - logo: optional image
    response:
      - application/pdf (download)
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        qc_raw = request.data.get("question_count", None)
        if qc_raw is None:
            return Response({"question_count": "required"}, status=400)

        try:
            question_count = int(str(qc_raw).strip())
        except Exception:
            return Response({"question_count": "must be an integer (1~45)"}, status=400)

        if question_count not in C.ALLOWED_QUESTION_COUNTS:
            return Response({"question_count": "must be between 1 and 45"}, status=400)

        logo = request.FILES.get("logo")
        if logo is not None:
            ctype = getattr(logo, "content_type", "") or ""
            if ctype and ctype not in C.ALLOWED_LOGO_CONTENT_TYPES:
                return Response({"logo": f"unsupported content_type: {ctype}"}, status=415)

        try:
            pdf_bytes = generate_objective_pdf(question_count=question_count, logo_file=logo)
        except LogoValidationError as e:
            return Response({"logo": str(e)}, status=400)
        except ValueError:
            return Response({"question_count": "must be between 1 and 45"}, status=400)

        resp = HttpResponse(pdf_bytes, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="omr_objective_v2_{question_count}.pdf"'
        return resp


class ObjectiveOMRMetaView(APIView):
    """
    GET /api/v1/assets/omr/objective/meta/?question_count=1..45
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qc_raw = request.query_params.get("question_count")
        if qc_raw is None:
            return Response({"question_count": "required"}, status=400)

        try:
            question_count = int(str(qc_raw).strip())
        except Exception:
            return Response({"question_count": "must be an integer (1~45)"}, status=400)

        if question_count not in C.ALLOWED_QUESTION_COUNTS:
            return Response({"question_count": "must be between 1 and 45"}, status=400)

        try:
            meta = build_objective_template_meta(question_count=question_count)
        except ValueError:
            return Response({"question_count": "must be between 1 and 45"}, status=400)

        return Response(meta, status=200)
