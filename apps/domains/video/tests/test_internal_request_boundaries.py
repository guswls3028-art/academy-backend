from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.domains.video.views.internal_views import VideoReconcileView


class VideoReconcileRequestBoundaryTests(SimpleTestCase):
    def test_rejects_ambiguous_boolean_before_running_command(self):
        request = SimpleNamespace(data={"dry_run": "flase"})

        with self.assertRaises(ValidationError):
            VideoReconcileView().post(request)

    def test_rejects_malformed_age_as_bad_request(self):
        request = SimpleNamespace(data={"older_than_minutes": "soon"})

        response = VideoReconcileView().post(request)

        self.assertEqual(response.status_code, 400)
