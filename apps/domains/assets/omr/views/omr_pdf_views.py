# PATH: apps/domains/assets/omr/views/omr_pdf_views.py
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from django.http import FileResponse

from apps.domains.exams.models import ExamAsset as Asset


class OMRPdfView(APIView):
    """
    OMR PDF 조회 — 인증 필수, Content-Disposition 명시.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, asset_id: int):
        asset = Asset.objects.get(id=asset_id)
        response = FileResponse(
            asset.file.open("rb"),
            content_type="application/pdf",
        )
        safe_name = f"omr_asset_{asset_id}.pdf"
        response["Content-Disposition"] = f'inline; filename="{safe_name}"'
        return response
