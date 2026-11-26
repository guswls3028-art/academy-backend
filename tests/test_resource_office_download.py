import runpy
import socket
from http.client import HTTPResponse, IncompleteRead
from pathlib import Path
from unittest.mock import Mock
from urllib.error import URLError

import pytest


@pytest.fixture
def office():
    return runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/install-resource-office.py"))


def test_office_fallback_keeps_exact_filename_and_checks_digest_before_unpacking(monkeypatch, office):
    install = office["install"]
    downloader = Mock(side_effect=[URLError("archive unavailable"), http_response(b"wrong bytes", None)])
    monkeypatch.setitem(install.__globals__, "urlopen", downloader)
    monkeypatch.setattr(office["platform"], "machine", lambda: "aarch64")
    with pytest.raises(ValueError, match="checksum mismatch"):
        install()
    first, second = [call.args[0] for call in downloader.call_args_list]
    assert first.startswith("https://downloadarchive.documentfoundation.org/")
    assert second.startswith("https://dev-builds.libreoffice.org/pre-releases/")
    assert first.split("/deb/")[1] == second.split("/deb/")[1]


def http_response(body, length):
    """A real HTTPResponse reading to EOF on a closed local socket."""
    header = b"" if length is None else b"Content-Length: " + length + b"\r\n"
    reader, writer = socket.socketpair()
    with reader, writer:
        writer.sendall(b"HTTP/1.0 200 OK\r\n" + header + b"\r\n" + body)
        writer.shutdown(socket.SHUT_WR)
        response = HTTPResponse(reader)
        response.begin()
        return response


def test_short_successful_http_response_retries_and_replaces_partial_file(monkeypatch, tmp_path, office):
    download = office["download_archive"]
    bodies = iter([(b"x" * 47, b"2100"), (b"complete", b"8")])
    downloader = Mock(side_effect=lambda *a, **kw: http_response(*next(bodies)))
    monkeypatch.setitem(download.__globals__, "urlopen", downloader)
    archive = tmp_path / "office.tar.gz"
    download(archive, "aarch64", "aarch64")
    assert downloader.call_count == 2
    assert archive.read_bytes() == b"complete"


@pytest.mark.parametrize("length", [None, b"not-a-length", b"-2", b"8"])
def test_missing_invalid_or_satisfied_length_does_not_trigger_retry(monkeypatch, tmp_path, length, office):
    download = office["download_archive"]
    downloader = Mock(side_effect=lambda *a, **kw: http_response(b"complete", length))
    monkeypatch.setitem(download.__globals__, "urlopen", downloader)
    archive = tmp_path / "office.tar.gz"
    download(archive, "aarch64", "aarch64")
    downloader.assert_called_once()
    assert archive.read_bytes() == b"complete"


def test_both_origins_short_raise_incomplete_read(monkeypatch, tmp_path, office):
    download = office["download_archive"]
    downloader = Mock(side_effect=lambda *a, **kw: http_response(b"x" * 47, b"2100"))
    monkeypatch.setitem(download.__globals__, "urlopen", downloader)
    with pytest.raises(IncompleteRead) as failure:
        download(tmp_path / "office.tar.gz", "aarch64", "aarch64")
    assert failure.value.expected == 2053
    assert downloader.call_count == 2
