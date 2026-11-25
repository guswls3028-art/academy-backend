import io
import runpy
from pathlib import Path
from unittest.mock import Mock
from urllib.error import URLError

import pytest


def test_office_fallback_keeps_exact_filename_and_checks_digest_before_unpacking(monkeypatch):
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/install-resource-office.py"))
    install = module["install"]
    downloader = Mock(side_effect=[URLError("archive unavailable"), io.BytesIO(b"wrong bytes")])
    monkeypatch.setitem(install.__globals__, "urlopen", downloader)
    monkeypatch.setattr(module["platform"], "machine", lambda: "aarch64")
    with pytest.raises(ValueError, match="checksum mismatch"):
        install()
    first, second = [call.args[0] for call in downloader.call_args_list]
    assert first.startswith("https://downloadarchive.documentfoundation.org/")
    assert second.startswith("https://dev-builds.libreoffice.org/pre-releases/")
    assert first.split("/deb/")[1] == second.split("/deb/")[1]
