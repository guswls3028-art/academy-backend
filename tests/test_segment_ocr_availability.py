"""OCR availability guard tests."""
from __future__ import annotations

from academy.adapters.ai.detection.segment_ocr import is_ocr_available


def test_ocr_unavailable_when_google_credentials_path_missing(monkeypatch):
    monkeypatch.delenv("GOOGLE_CREDENTIALS_JSON", raising=False)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "C:/missing/ocr.json")

    assert is_ocr_available() is False


def test_ocr_available_when_google_credentials_path_exists(tmp_path, monkeypatch):
    creds = tmp_path / "ocr.json"
    creds.write_text("{}", encoding="utf-8")
    monkeypatch.delenv("GOOGLE_CREDENTIALS_JSON", raising=False)
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(creds))

    assert is_ocr_available() is True


def test_ocr_unavailable_when_credentials_json_invalid(monkeypatch):
    monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", "{not-json")
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    assert is_ocr_available() is False


def test_ocr_available_when_credentials_json_valid(monkeypatch):
    monkeypatch.setenv("GOOGLE_CREDENTIALS_JSON", '{"client_email":"x"}')
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)

    assert is_ocr_available() is True
