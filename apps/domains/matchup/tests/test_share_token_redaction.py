from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.test import RequestFactory, SimpleTestCase

from apps.domains.matchup.views_hit_report import HitReportSharePdfView


class HitReportShareTokenRedactionTests(SimpleTestCase):
    def test_pdf_generation_failure_does_not_log_bearer_token(self):
        token = uuid4()
        report = SimpleNamespace(
            id=42,
            share_token=token,
            title="",
            document=None,
        )
        request = RequestFactory().get(f"/api/v1/matchup/share/{token}/curated.pdf")

        with (
            patch(
                "apps.domains.matchup.views_hit_report."
                "MatchupHitReport.objects.select_related"
            ) as select_related,
            patch(
                "apps.domains.matchup.views_hit_report._share_etag",
                return_value='W/"redacted"',
            ),
            patch(
                "apps.domains.matchup.views_hit_report."
                "_get_or_generate_curated_hit_report_pdf",
                side_effect=RuntimeError("render failed"),
            ),
            self.assertLogs(
                "apps.domains.matchup.views_hit_report",
                level="ERROR",
            ) as captured,
        ):
            select_related.return_value.get.return_value = report
            response = HitReportSharePdfView.as_view()(request, token=token)

        self.assertEqual(response.status_code, 500)
        joined = "\n".join(captured.output)
        self.assertIn("report=42", joined)
        self.assertNotIn(str(token), joined)
