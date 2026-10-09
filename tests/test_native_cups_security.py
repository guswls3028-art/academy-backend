"""A package label or an empty conversion must not pass the native gate."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


SPEC = importlib.util.spec_from_file_location(
    "verify_cups", Path(__file__).parents[1] / "docker/native-security/verify-cups.py"
)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


@pytest.mark.parametrize(
    "package",
    [
        "2.4.10-3+deb13u2\nlibssl3t64",
        "2.4.10-3+deb13u2+academy1\nlibgnutls30t64",
        "2.4.10-3+deb13u2+academy1\nlibssl3t64, libgnutls30t64",
    ],
)
def test_rejects_wrong_package_or_dependency(monkeypatch, package):
    monkeypatch.setattr(verifier.subprocess, "check_output", lambda *args, **kwargs: package)
    with pytest.raises(RuntimeError, match="package or TLS dependency"):
        verifier.verify()


def test_rejects_a_physical_gnutls_library_despite_relabelled_metadata(monkeypatch):
    monkeypatch.setattr(
        verifier.subprocess, "check_output", lambda *args, **kwargs: "2.4.10-3+deb13u2+academy1\nlibssl3t64"
    )
    monkeypatch.setattr(verifier.Path, "glob", lambda *args: iter([Path("libgnutls.so.30")]))
    with pytest.raises(RuntimeError, match="GnuTLS runtime library"):
        verifier.verify()


@pytest.mark.parametrize(
    "text,pages", [("", 1), ("Academy document conversion", 1), ("Academy document conversion 수업 분석 자료", 0)]
)
def test_office_success_exit_without_complete_content_is_rejected(monkeypatch, text, pages):
    class Document:
        page_count = pages

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            return iter([SimpleNamespace(get_text=lambda: text)])

    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(open=lambda *args: Document()))
    monkeypatch.setattr(verifier.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    with pytest.raises(RuntimeError, match="lost the document content"):
        verifier.verify_office()
