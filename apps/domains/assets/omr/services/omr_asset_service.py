from django.core.files import File

from apps.domains.assets.models import Asset
from .render_service import OMRRenderService


class OMRAssetService:
    """
    시험 OMR PDF 생성 + Asset 저장
    """

    @staticmethod
    def create_exam_omr(
        *,
        exam,
        question_count: int = 45,
    ) -> Asset:
        pdf_path = OMRRenderService.render(
            question_count=question_count,
            debug_grid=False,
        )

        with open(pdf_path, "rb") as f:
            asset = Asset.objects.create(
                exam=exam,
                asset_type=Asset.AssetType.OMR_SHEET,
                file=File(f, name="omr_v245_final.pdf"),
            )

        return asset
