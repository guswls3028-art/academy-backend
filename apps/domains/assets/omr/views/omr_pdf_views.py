# PATH: apps/domains/assets/omr/views/omr_pdf_views.py
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.exceptions import NotFound
from django.http import FileResponse

from apps.domains.exams.models import ExamAsset as Asset


class OMRPdfView(APIView):
    """
    OMR PDF 조회 — 인증 필수, 테넌트 격리 적용, Content-Disposition 명시.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, asset_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            raise NotFound("테넌트 정보가 없습니다.")

        asset = Asset.objects.filter(
            id=asset_id,
            exam__sessions__lecture__tenant=tenant,
        ).first()
        if not asset:
            raise NotFound("해당 자료를 찾을 수 없습니다.")

        response = FileResponse(
            asset.file.open("rb"),
            content_type="application/pdf",
        )
        safe_name = f"omr_asset_{asset_id}.pdf"
        response["Content-Disposition"] = f'inline; filename="{safe_name}"'
        return response
