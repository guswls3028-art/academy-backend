import io
from types import SimpleNamespace
from unittest.mock import Mock, patch

import fitz
from django.test import RequestFactory, SimpleTestCase
from PIL import Image

from apps.domains.matchup.views_hit_report import (
    HitReportLandingPublicPreviewView,
    _prewarm_hit_report_preview_if_public,
)
from apps.support.landing_public.matchup_preview import (
    get_or_create_matchup_preview,
    preview_etag_for_pdf,
    render_matchup_pdf_preview,
)


def _two_page_pdf() -> bytes:
    document = fitz.open()
    first = document.new_page(width=300, height=200)
    first.draw_rect(first.rect, color=(1, 0, 0), fill=(1, 0, 0))
    second = document.new_page(width=300, height=200)
    second.draw_rect(second.rect, color=(0, 1, 0), fill=(0, 1, 0))
    data = document.tobytes()
    document.close()
    return data


class MatchupPreviewRenderingTests(SimpleTestCase):
    def test_two_page_report_uses_first_comparison_page(self):
        image_bytes = render_matchup_pdf_preview(_two_page_pdf())

        with Image.open(io.BytesIO(image_bytes)) as image:
            red, green, blue = image.convert("RGB").getpixel((image.width // 2, image.height // 2))

        self.assertGreater(green, 220)
        self.assertLess(red, 35)
        self.assertLess(blue, 35)

    def test_user_upload_uses_first_pdf_page(self):
        image_bytes = render_matchup_pdf_preview(
            _two_page_pdf(),
            first_body_page=False,
        )

        with Image.open(io.BytesIO(image_bytes)) as image:
            red, green, blue = image.convert("RGB").getpixel((image.width // 2, image.height // 2))

        self.assertGreater(red, 220)
        self.assertLess(green, 35)
        self.assertLess(blue, 35)

    def test_large_media_box_is_rendered_with_bounded_dimensions(self):
        document = fitz.open()
        document.new_page(width=20_000, height=20_000)
        data = document.tobytes()
        document.close()

        image_bytes = render_matchup_pdf_preview(data, first_body_page=False)

        with Image.open(io.BytesIO(image_bytes)) as image:
            self.assertLessEqual(max(image.size), 4096)
            self.assertLessEqual(image.width * image.height, 12_000_000)

    def test_cached_preview_skips_pdf_loading(self):
        load_pdf = Mock(side_effect=AssertionError("PDF should not be loaded"))
        with patch(
            "apps.infrastructure.storage.r2.get_object_bytes_r2_storage",
            return_value=b"cached-jpeg",
        ):
            result, cache_state = get_or_create_matchup_preview(
                pdf_key="reports/example.pdf",
                load_pdf_bytes=load_pdf,
            )

        self.assertEqual(result, b"cached-jpeg")
        self.assertEqual(cache_state, "hit")
        load_pdf.assert_not_called()

    def test_preview_etag_changes_with_render_version(self):
        with patch(
            "apps.support.landing_public.matchup_preview._PREVIEW_RENDER_VERSION",
            "v2",
        ):
            first = preview_etag_for_pdf("reports/example.pdf")
        with patch(
            "apps.support.landing_public.matchup_preview._PREVIEW_RENDER_VERSION",
            "v3",
        ):
            second = preview_etag_for_pdf("reports/example.pdf")

        self.assertNotEqual(first, second)


class LandingHitReportPreviewViewTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_public_preview_returns_static_jpeg_with_cache_headers(self):
        report = SimpleNamespace(id=42)
        request = self.factory.get("/api/v1/matchup/landing/public/42/preview.jpg?tenant=demo")

        with (
            patch(
                "apps.domains.matchup.views_hit_report._resolve_landing_pdf_tenant",
                return_value=SimpleNamespace(id=1),
            ),
            patch(
                "apps.domains.matchup.views_hit_report._is_report_in_published_landing",
                return_value=True,
            ),
            patch(
                "apps.domains.matchup.views_hit_report.MatchupHitReport.objects.select_related",
            ) as select_related,
            patch(
                "apps.domains.matchup.views_hit_report._hit_report_preview_etag",
                return_value='W/"preview"',
            ),
            patch(
                "apps.domains.matchup.views_hit_report._get_cached_hit_report_preview",
                return_value=b"jpeg-bytes",
            ),
        ):
            select_related.return_value.get.return_value = report
            response = HitReportLandingPublicPreviewView.as_view()(request, report_id=42)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/jpeg")
        self.assertEqual(response["X-Matchup-Preview-Cache"], "hit")
        self.assertEqual(response.content, b"jpeg-bytes")

    def test_public_cache_miss_never_generates_pdf_in_request(self):
        report = SimpleNamespace(id=42)
        request = self.factory.get("/api/v1/matchup/landing/public/42/preview.jpg?tenant=demo")

        with (
            patch(
                "apps.domains.matchup.views_hit_report._resolve_landing_pdf_tenant",
                return_value=SimpleNamespace(id=1),
            ),
            patch(
                "apps.domains.matchup.views_hit_report._is_report_in_published_landing",
                return_value=True,
            ),
            patch(
                "apps.domains.matchup.views_hit_report.MatchupHitReport.objects.select_related",
            ) as select_related,
            patch(
                "apps.domains.matchup.views_hit_report._hit_report_preview_etag",
                return_value='W/"preview"',
            ),
            patch(
                "apps.domains.matchup.views_hit_report._get_cached_hit_report_preview",
                return_value=None,
            ),
            patch(
                "apps.domains.matchup.views_hit_report._get_or_generate_hit_report_preview",
            ) as render_mock,
        ):
            select_related.return_value.get.return_value = report
            response = HitReportLandingPublicPreviewView.as_view()(request, report_id=42)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Cache-Control"], "no-store")
        render_mock.assert_not_called()

    def test_public_report_edit_requires_strict_preview_refresh(self):
        report = SimpleNamespace(id=42, share_token=None)

        with (
            patch(
                "apps.domains.matchup.views_hit_report._is_report_in_published_landing",
                return_value=True,
            ),
            patch(
                "apps.domains.matchup.views_hit_report."
                "_get_or_generate_hit_report_preview",
                return_value=(b"jpeg-bytes", "miss"),
            ) as prewarm,
        ):
            _prewarm_hit_report_preview_if_public(
                report,
                tenant=SimpleNamespace(id=1),
            )

        self.assertTrue(prewarm.call_args.kwargs["require_cache_write"])
