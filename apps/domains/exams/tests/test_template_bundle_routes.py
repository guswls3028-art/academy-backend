from django.test import SimpleTestCase
from django.urls import resolve

from apps.domains.exams.views.template_bundle_view import (
    ApplyBundleView,
    TemplateBundleViewSet,
)


class TemplateBundleRoutePrecedenceTests(SimpleTestCase):
    def test_bundle_list_route_resolves_before_exam_detail_router(self):
        match = resolve("/api/v1/exams/bundles/")

        self.assertIs(getattr(match.func, "cls", None), TemplateBundleViewSet)
        self.assertEqual(match.kwargs, {})

    def test_bundle_apply_route_resolves_before_exam_detail_router(self):
        match = resolve("/api/v1/exams/bundles/123/apply/")

        self.assertIs(getattr(match.func, "view_class", None), ApplyBundleView)
        self.assertEqual(match.kwargs, {"bundle_id": 123})
