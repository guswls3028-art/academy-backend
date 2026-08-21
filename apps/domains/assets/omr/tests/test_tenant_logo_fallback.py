from pathlib import Path

from django.test import SimpleTestCase

from apps.domains.assets.omr.dto.omr_document import OMRDocument
from apps.domains.assets.omr.services.omr_document_service import OMRDocumentService


class TenantLogoFallbackTests(SimpleTestCase):
    def test_godmin_pdf_fallback_uses_same_tenant_logo_asset(self):
        tenant = type("TenantStub", (), {"code": "godmin"})()
        document = OMRDocument(exam_title="시험", logo_url="")

        resolved = OMRDocumentService.fetch_logo_bytes(document, tenant=tenant)

        expected = (
            Path(__file__).resolve().parents[1]
            / "renderer"
            / "logos"
            / "godmin.png"
        ).read_bytes()
        self.assertEqual(resolved.logo_bytes, expected)
        self.assertEqual(resolved.logo_mime, "image/png")
