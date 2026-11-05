"""Exercise production origin policy through the real CORS middleware."""

import json
import os
from pathlib import Path
import subprocess
import sys

from corsheaders.middleware import CorsMiddleware
from django.http import HttpResponse
from django.test import RequestFactory, override_settings
import pytest


@pytest.fixture(scope="module")
def production_cors():
    # Use synthetic signing keys without changing the parent Django settings.
    env = os.environ.copy()
    env.update({
        "SECRET_KEY": "test-production-secret-key-with-safe-length",
        "MESSAGING_TENANT_BINDING_KEY": "test-production-messaging-binding-key",
        "CDN_HLS_BASE_URL": "https://cdn.hakwonplus.com",
        "CDN_HLS_SIGNING_SECRET": "test-production-video-signing-secret",
        "CDN_HLS_SIGNING_KEY_ID": "v1",
        "TOSS_AUTO_BILLING_ENABLED": "false",
        "BILLING_KEY_ENCRYPTION_WRITE_ENABLED": "false",
        "BILLING_KEY_ENCRYPTION_PRIMARY_KEY": "",
        "BILLING_KEY_ENCRYPTION_FALLBACK_KEYS": "",
        "BILLING_BANK_TRANSFER_ENABLED": "false",
    })
    code = (
        "import json; import apps.api.config.settings.prod as s; "
        "print(json.dumps({k: getattr(s, k) for k in "
        "('CORS_ALLOWED_ORIGINS', 'CORS_ALLOWED_ORIGIN_REGEXES', "
        "'CORS_ALLOW_ALL_ORIGINS', 'CORS_ALLOW_CREDENTIALS', 'CORS_ALLOW_HEADERS')}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env, capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("method", ["GET", "OPTIONS"])
@pytest.mark.parametrize("origin,allowed", [
    ("https://hakwonplus.com", True),
    ("https://tchul.com", True),
    ("https://academy-frontend.pages.dev", True),
    ("https://a1b2c3d4.academy-frontend.pages.dev", True),
    ("https://production-candidate-123-1.academy-frontend.pages.dev", True),
    ("https://academy-frontend-attacker.pages.dev", False),
    ("https://attacker-academy-frontend.pages.dev", False),
    ("https://academy-frontend.pages.dev.attacker.example", False),
    ("https://preview.other-project.pages.dev", False),
    ("https://nested.preview.academy-frontend.pages.dev", False),
    ("http://a1b2c3d4.academy-frontend.pages.dev", False),
    ("https://a1b2c3d4.academy-frontend.pages.dev:444", False),
    ("null", False),
])
def test_production_cors_origin_boundary(production_cors, method, origin, allowed):
    request = RequestFactory().generic(
        method, "/api/v1/core/me/", HTTP_ORIGIN=origin,
        HTTP_ACCESS_CONTROL_REQUEST_METHOD="GET",
        HTTP_ACCESS_CONTROL_REQUEST_HEADERS="authorization,x-tenant-code",
    )
    with override_settings(**production_cors):
        # Allowing an origin must not turn an unauthenticated response into success.
        response = CorsMiddleware(lambda _: HttpResponse(status=401))(request)
    assert response.status_code == (200 if method == "OPTIONS" else 401)
    if allowed:
        assert response["Access-Control-Allow-Origin"] == origin
        assert response["Access-Control-Allow-Credentials"] == "true"
        if method == "OPTIONS":
            assert "authorization" in response["Access-Control-Allow-Headers"]
            assert "GET" in response["Access-Control-Allow-Methods"]
    else:
        assert "Access-Control-Allow-Origin" not in response
        assert "Access-Control-Allow-Credentials" not in response
