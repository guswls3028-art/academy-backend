from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_settings(relative_path: str):
    path = BACKEND_ROOT / relative_path
    module_name = f"_settings_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_database_ssl_mode_is_opt_in(monkeypatch):
    monkeypatch.delenv("DB_SSL_MODE", raising=False)

    base = _load_settings("apps/api/config/settings/base.py")
    worker = _load_settings("apps/api/config/settings/worker.py")

    assert base.DATABASES["default"]["OPTIONS"] == {"connect_timeout": 10}
    assert worker.DATABASES["default"]["OPTIONS"] == {"connect_timeout": 10}


def test_database_ssl_mode_is_passed_to_postgres_options(monkeypatch):
    monkeypatch.setenv("DB_SSL_MODE", "require")

    base = _load_settings("apps/api/config/settings/base.py")
    worker = _load_settings("apps/api/config/settings/worker.py")

    assert base.DATABASES["default"]["OPTIONS"]["sslmode"] == "require"
    assert worker.DATABASES["default"]["OPTIONS"]["sslmode"] == "require"
