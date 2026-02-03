# PATH: apps/domains/assets/omr/views/omr_pdf_views.py
from rest_framework.views import APIView
from rest_framework.response import Response
from django.http import FileResponse

from apps.domains.exams.models import ExamAsset as Asset


class OMRPdfView(APIView):
    """
    OMR PDF 조회
    """

    def get(self, request, asset_id: int):
        asset = Asset.objects.get(id=asset_id)
        return FileResponse(asset.file.open("rb"), content_type="application/pdf")
