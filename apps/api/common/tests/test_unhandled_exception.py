"""
UnhandledExceptionMiddleware 회귀 테스트.

검증 항목:
- DEBUG=False(운영): 응답 페이로드에 내부 예외 메시지(`error`)가 노출되지 않는다.
- DEBUG=True(개발): 디버깅을 위해 `error` 필드가 포함된다.
- 두 모드 모두 `correlation_id` 가 항상 포함된다.
"""

import json

from django.contrib.admin.sites import AdminSite
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import path

from apps.api.common.middleware import UnhandledExceptionMiddleware


def _deny_access(request):
    raise PermissionDenied


urlpatterns = [
    path("admin/denied/", _deny_access),
    path("admin/<path:url>", AdminSite().catch_all_view),
]


@override_settings(
    ROOT_URLCONF=__name__,
    DEBUG=False,
    MIDDLEWARE=[
        "apps.api.common.middleware.CorsResponseFixMiddleware",
        "apps.api.common.middleware.UnhandledExceptionMiddleware",
    ],
    CORS_ALLOWED_ORIGINS=["https://school.example"],
)
class NativeHttpStatusTests(SimpleTestCase):
    def test_expected_denials_reach_the_client_with_their_status(self):
        for url, expected in (("/admin/missing/", 404), ("/admin/denied/", 403)):
            with self.subTest(url=url):
                response = self.client.get(url, HTTP_ORIGIN="https://school.example")
                self.assertEqual(response.status_code, expected)
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], "https://school.example")


def _request() -> HttpRequest:
    req = HttpRequest()
    req.method = "GET"
    req.path = "/api/v1/test/"
    req.META["HTTP_ORIGIN"] = ""
    return req


class TestUnhandledExceptionMiddleware(TestCase):

    def setUp(self):
        self.mw = UnhandledExceptionMiddleware(get_response=lambda req: None)

    @override_settings(DEBUG=False)
    def test_prod_does_not_leak_exception_string(self):
        resp = self.mw.process_exception(
            _request(), ValueError("internal db error: secret_table.column"),
        )
        self.assertEqual(resp.status_code, 500)
        body = json.loads(resp.content)
        self.assertIn("detail", body)
        self.assertIn("correlation_id", body)
        self.assertNotIn("error", body)

    @override_settings(DEBUG=True)
    def test_debug_includes_exception_string(self):
        resp = self.mw.process_exception(
            _request(), ValueError("dev hint: traceback path"),
        )
        self.assertEqual(resp.status_code, 500)
        body = json.loads(resp.content)
        self.assertIn("detail", body)
        self.assertIn("correlation_id", body)
        self.assertEqual(body["error"], "dev hint: traceback path")
