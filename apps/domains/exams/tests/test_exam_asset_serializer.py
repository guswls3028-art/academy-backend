from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.domains.exams.serializers.exam_asset import ExamAssetSerializer


class ExamAssetSerializerTests(SimpleTestCase):
    @patch(
        "apps.domains.exams.serializers.exam_asset.generate_presigned_get_url_storage",
        return_value="https://storage.test/source",
    )
    @patch("apps.domains.exams.serializers.exam_asset.generate_presigned_get_url")
    def test_pdf_extract_asset_downloads_from_storage_bucket(
        self,
        ai_presign,
        storage_presign,
    ):
        asset = SimpleNamespace(
            exam=SimpleNamespace(tenant_id=4),
            file_key="tenants/4/exams/pdf-extract/run/source.hwp",
        )

        url = ExamAssetSerializer().get_download_url(asset)

        self.assertEqual(url, "https://storage.test/source")
        storage_presign.assert_called_once_with(
            key=asset.file_key,
            expires_in=60 * 60,
        )
        ai_presign.assert_not_called()

    @patch(
        "apps.domains.exams.serializers.exam_asset.generate_presigned_get_url",
        return_value="https://ai.test/asset",
    )
    @patch("apps.domains.exams.serializers.exam_asset.generate_presigned_get_url_storage")
    def test_direct_exam_asset_downloads_from_ai_bucket(
        self,
        storage_presign,
        ai_presign,
    ):
        asset = SimpleNamespace(
            exam=SimpleNamespace(tenant_id=4),
            file_key="tenants/4/ai/exams/763/assets/problem_pdf/source.pdf",
        )

        url = ExamAssetSerializer().get_download_url(asset)

        self.assertEqual(url, "https://ai.test/asset")
        ai_presign.assert_called_once_with(
            key=asset.file_key,
            expires_in=60 * 60,
        )
        storage_presign.assert_not_called()
