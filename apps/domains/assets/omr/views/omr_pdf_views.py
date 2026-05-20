# PATH: apps/domains/assets/omr/views/omr_pdf_views.py
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.exceptions import NotFound
from django.db.models import Q
from django.http import HttpResponseRedirect

from apps.core.permissions import TenantResolvedAndMember
from apps.domains.exams.models import ExamAsset as Asset
from apps.infrastructure.storage.r2 import generate_presigned_get_url


class OMRPdfView(APIView):
    """
    OMR PDF 조회 — 인증/테넌트 멤버십 확인 후 R2 presigned URL로 이동.
    """
    permission_classes = [IsAuthenticated, TenantResolvedAndMember]

    def get(self, request, asset_id: int):
        tenant = getattr(request, "tenant", None)
        if not tenant:
            raise NotFound("테넌트 정보가 없습니다.")

        asset = Asset.objects.filter(
            id=asset_id,
            asset_type=Asset.AssetType.OMR_SHEET,
        ).filter(
            Q(exam__tenant=tenant)
            | Q(exam__sessions__lecture__tenant=tenant)
            | Q(exam__derived_exams__sessions__lecture__tenant=tenant)
        ).first()
        if not asset:
            raise NotFound("해당 자료를 찾을 수 없습니다.")

        url = generate_presigned_get_url(
            key=asset.file_key,
            expires_in=60 * 10,
        )
        return HttpResponseRedirect(url)
