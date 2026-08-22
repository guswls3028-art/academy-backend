"""Initial-password state is advisory and must never install an API gate."""

from django.conf import settings
from django.test import SimpleTestCase


class TestPasswordChangeRecommendationContract(SimpleTestCase):
    def test_must_change_password_middleware_is_not_installed(self):
        self.assertNotIn(
            "apps.api.common.middleware.MustChangePasswordGate",
            settings.MIDDLEWARE,
        )
