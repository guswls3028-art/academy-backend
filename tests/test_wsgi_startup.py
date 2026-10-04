"""Request routing must be ready before a fresh WSGI worker serves traffic."""

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def run_fresh_wsgi(code):
    return subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env={
            **os.environ,
            "DJANGO_SETTINGS_MODULE": "apps.api.config.settings.test",
            "AWS_EC2_METADATA_DISABLED": "true",
            "PYTHONIOENCODING": "utf-8",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


def test_fresh_worker_has_loaded_real_routes_before_first_request():
    result = run_fresh_wsgi(
        "from apps.api.config.wsgi import application\n"
        "from django.urls import get_resolver\n"
        "assert callable(application)\n"
        "assert 'url_patterns' in vars(get_resolver()), 'routes still load on first request'\n"
    )
    assert result.returncode == 0, result.stderr


def test_invalid_routes_fail_worker_startup():
    result = run_fresh_wsgi(
        "from django.conf import settings\n"
        "settings.ROOT_URLCONF = 'academy_missing_startup_routes'\n"
        "from apps.api.config.wsgi import application\n"
    )
    assert result.returncode != 0
    assert "academy_missing_startup_routes" in result.stderr
