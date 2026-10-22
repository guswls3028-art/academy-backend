import json
import uuid
from io import StringIO
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase
from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APIRequestFactory

from academy.adapters.youtube_statistics import StatisticsUnavailable, fetch_youtube_statistics
from apps.core.models import Tenant
from apps.domains.landing_public.api.views.video_views import PublicVideoDetailView, PublicVideoListView
from apps.domains.landing_public.models.video import PublicVideo
from apps.domains.landing_public.services.video_statistics import public_statistics, refresh_video_statistics


class VideoBrowseTests(TestCase):
    def setUp(self):
        self.tenant = Tenant.objects.create(name="qa-browse", code="qa-browse", is_active=True)
        self.other = Tenant.objects.create(name="qa-browse-other", code="qa-browse-other", is_active=True)
        self.now = timezone.now()
        self.environment = patch.dict("os.environ", {"YOUTUBE_DATA_API_KEY": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def video(self, index, **values):
        content = {"title": f"영상 {index}", "summary": "", "category": "interview", "category_label": "과학", "format": "landscape" if index % 2 == 0 else "vertical"}
        return PublicVideo.objects.create(**{
            "tenant": self.tenant, "youtube_video_id": f"v{index:010d}", "canonical_url": f"https://www.youtube.com/watch?v=v{index:010d}",
            "draft_content": {**content, "title": "공개되지 않은 초안"}, "published_content": content,
            "status": "published", "request_id": uuid.uuid4(), "published_at": self.now,
            "youtube_published_at": self.now - timedelta(days=index), "youtube_view_count": index * 10,
            "youtube_statistics_checked_at": self.now, **values,
        })

    def browse(self, params=None):
        request = APIRequestFactory().get("/api/v1/landing-public/videos/", params or {})
        request.tenant = self.tenant
        return PublicVideoListView.as_view()(request)

    def test_global_format_sort_and_pagination_keep_tenant_and_public_snapshot(self):
        rows = [self.video(i) for i in range(30)]
        self.video(90, tenant=self.other)
        self.video(92, status="draft")
        self.video(94, status="deleted")
        self.video(96, status="unpublished")
        result = self.browse({"kind": "landscape", "sort": "popular"})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data["count"], 15)
        self.assertEqual([item["id"] for item in result.data["results"]], [str(row.pk) for row in rows[::2][::-1][:12]])
        self.assertIn("kind=landscape", result.data["next"])
        self.assertIn("sort=popular", result.data["next"])
        second = self.browse({"kind": "landscape", "sort": "popular", "page": 2})
        self.assertEqual([item["youtube_view_count"] for item in second.data["results"]], [40, 20, 0])
        oldest = self.browse({"kind": "vertical", "sort": "oldest"})
        self.assertEqual(oldest.data["results"][0]["id"], str(rows[-1].pk))
        latest = self.browse({"kind": "vertical", "sort": "latest"})
        self.assertEqual(latest.data["results"][0]["id"], str(rows[1].pk))
        self.assertEqual(self.browse({"q": "공개되지 않은 초안"}).data["count"], 0)
        self.assertEqual(self.browse({"q": "과학", "kind": "vertical"}).data["count"], 15)
        self.assertEqual(self.browse().data["count"], 30)  # Old clients still see both formats.

    def test_unknown_statistics_are_not_zero_and_expired_data_is_not_ranked(self):
        unknown = self.video(0, youtube_view_count=None, youtube_statistics_checked_at=None)
        expired = self.video(2, youtube_view_count=999999, youtube_statistics_checked_at=self.now - timedelta(days=31))
        self.assertEqual(self.browse({"sort": "popular"}).status_code, 503)
        known = self.video(4, youtube_view_count=0)
        result = self.browse({"sort": "popular"})
        self.assertEqual(result.data["results"][0]["id"], str(known.pk))
        self.assertTrue(all(row["youtube_view_count"] is None for row in result.data["results"][1:]))
        expired.refresh_from_db()
        self.assertIsNone(expired.youtube_view_count)
        self.assertIsNone(public_statistics(unknown)["youtube_view_count"])
        self.assertEqual(self.browse({"sort": "popular", "q": "없는 제목"}).status_code, 200)

    def test_invalid_filters_fail_instead_of_silently_showing_other_results(self):
        for params in ({"kind": "invalid"}, {"sort": "invalid"}, {"q": "a" * 91}):
            self.assertEqual(self.browse(params).status_code, 400)

    def test_retention_clears_only_expired_provider_fields_across_visibility_and_tenants(self):
        old = [self.video(i, status=status, youtube_statistics_checked_at=self.now - timedelta(days=29),
                          tenant=self.other if i % 2 else self.tenant)
               for i, status in enumerate(("published", "draft", "unpublished", "deleted"))]
        recent = self.video(10)
        call_command("expire_public_video_statistics", stdout=StringIO())
        self.assertEqual(PublicVideo.objects.exclude(youtube_statistics_checked_at=None).count(), 5)
        call_command("expire_public_video_statistics", execute=True, stdout=StringIO())
        for row in old:
            row.refresh_from_db()
            self.assertIsNone(row.youtube_view_count)
            self.assertIsNone(row.youtube_published_at)
            self.assertIsNone(row.youtube_statistics_retry_at)
            self.assertEqual(row.revision, 1)
            self.assertEqual(row.draft_content["title"], "공개되지 않은 초안")
        recent.refresh_from_db()
        self.assertEqual(recent.youtube_view_count, 100)

    @patch.dict("os.environ", {"YOUTUBE_DATA_API_KEY": "qa-test-only"})
    @patch("apps.domains.landing_public.services.video_statistics.fetch_youtube_statistics")
    def test_direct_detail_refreshes_only_the_visible_target_and_returns_current_statistics(self, fetch):
        row = self.video(0, youtube_view_count=None, youtube_statistics_checked_at=None)
        self.video(1)
        fetch.return_value = {row.youtube_video_id: {"view_count": 123, "published_at": self.now}}
        request = APIRequestFactory().get(f"/api/v1/landing-public/videos/{row.pk}/")
        request.tenant = self.tenant
        result = PublicVideoDetailView.as_view()(request, pk=row.pk)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data["youtube_view_count"], 123)
        fetch.assert_called_once_with([row.youtube_video_id], "qa-test-only")

    @patch.dict("os.environ", {"YOUTUBE_DATA_API_KEY": "qa-test-only"})
    @patch("apps.domains.landing_public.services.video_statistics.fetch_youtube_statistics")
    def test_refresh_updates_only_derived_fields_and_claims_once(self, fetch):
        row = self.video(0)
        self.video(1, tenant=self.other)
        fetch.return_value = {row.youtube_video_id: {"view_count": 1234, "published_at": self.now - timedelta(days=12)}}
        self.assertEqual(refresh_video_statistics(self.tenant), 1)
        self.assertEqual(refresh_video_statistics(self.tenant), 0)
        fetch.assert_called_once_with([row.youtube_video_id], "qa-test-only")
        row.refresh_from_db()
        self.assertEqual(row.youtube_view_count, 1234)
        self.assertEqual(row.revision, 1)
        self.assertEqual(row.draft_content["title"], "공개되지 않은 초안")
        self.assertEqual(row.published_content["title"], "영상 0")
        self.assertEqual(row.published_at, self.now)

    @patch.dict("os.environ", {"YOUTUBE_DATA_API_KEY": "qa-test-only"})
    @patch("apps.domains.landing_public.services.video_statistics.fetch_youtube_statistics")
    def test_outage_preserves_last_good_statistics_and_does_not_hammer_provider(self, fetch):
        row = self.video(0, youtube_view_count=33)
        fetch.side_effect = StatisticsUnavailable()
        self.assertEqual(refresh_video_statistics(self.tenant), 0)
        self.assertEqual(refresh_video_statistics(self.tenant), 0)
        self.assertEqual(fetch.call_count, 1)
        row.refresh_from_db()
        self.assertEqual(row.youtube_view_count, 33)

    @patch.dict("os.environ", {"YOUTUBE_DATA_API_KEY": "qa-test-only"})
    @patch("apps.domains.landing_public.services.video_statistics.fetch_youtube_statistics")
    def test_delete_during_fetch_is_not_revived(self, fetch):
        row = self.video(0)
        def deleted(*args):
            PublicVideo.objects.filter(pk=row.pk).update(status="deleted")
            return {row.youtube_video_id: {"view_count": 999, "published_at": self.now}}
        fetch.side_effect = deleted
        refresh_video_statistics(self.tenant)
        row.refresh_from_db()
        self.assertEqual(row.status, "deleted")
        self.assertEqual(row.youtube_view_count, 0)


class YouTubeStatisticsTransportTests(SimpleTestCase):
    @patch("academy.adapters.youtube_statistics.requests.get")
    def test_bounded_official_endpoint_and_absent_vs_zero(self, get):
        response = MagicMock()
        get.return_value.__enter__.return_value = response
        response.status_code = 200
        response.iter_content.return_value = [json.dumps({"items": [{"id": "xgjL2HyrdFM", "snippet": {"publishedAt": "2026-10-13T12:00:00Z"}, "statistics": {"viewCount": "0"}}]}).encode()]
        result = fetch_youtube_statistics(["xgjL2HyrdFM", "YYTULV4h4Io"], "qa-not-a-secret")
        self.assertEqual(result["xgjL2HyrdFM"]["view_count"], 0)
        self.assertNotIn("YYTULV4h4Io", result)
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://www.googleapis.com/youtube/v3/videos")
        self.assertNotIn("key", kwargs["params"])
        self.assertFalse(kwargs["allow_redirects"])
        self.assertEqual(kwargs["timeout"], (3, 5))

    @patch("academy.adapters.youtube_statistics.requests.get")
    def test_redirect_malformed_and_oversize_responses_fail_closed(self, get):
        response = MagicMock()
        get.return_value.__enter__.return_value = response
        for status, body in [(302, b""), (200, b"x" * 262145), (200, b'{"items":{}}'), (200, b'{"items":[{"id":"outside-id"}]}')]:
            response.status_code = status
            response.iter_content.return_value = [body]
            with self.assertRaises(StatisticsUnavailable):
                fetch_youtube_statistics(["xgjL2HyrdFM"], "qa-not-a-secret")
        with self.assertRaises(StatisticsUnavailable):
            fetch_youtube_statistics(["https://evil.invalid/"], "qa-not-a-secret")
