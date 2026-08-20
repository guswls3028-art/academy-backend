from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework.exceptions import ValidationError

from apps.domains.landing_public.api.views.board_views import PublicBoardPostViewSet
from apps.domains.landing_public.api.views.reply_views import PublicPostReplyViewSet


class LandingBooleanMutationBoundaryTests(SimpleTestCase):
    def test_board_moderation_rejects_ambiguous_boolean(self):
        view = PublicBoardPostViewSet()
        view.get_object = lambda: SimpleNamespace()
        request = SimpleNamespace(data={"external_visible": "flase"})

        with self.assertRaises(ValidationError):
            view.moderate(request)

    def test_reply_visibility_rejects_ambiguous_boolean(self):
        view = PublicPostReplyViewSet()
        view.get_object = lambda: SimpleNamespace()
        request = SimpleNamespace(data={"is_hidden": "flase"})

        with self.assertRaises(ValidationError):
            view.hide(request)
