"""Offline failure boundaries; actual benign OCR is required in image builds."""
import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_tesseract", ROOT / "docker/native-security/verify-tesseract.py",
)
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


@pytest.mark.parametrize("fault", [None, "version", "source", "cli", "language", "eng", "kor"])
def test_installed_package_and_both_language_controls_are_required(monkeypatch, fault, capsys):
    calls = []

    def read(args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] <= 30
        if args[0] == "dpkg-query":
            if args[2] == "-f=${Version}":
                return "5.5.0-1" if fault == "version" else verifier.VERSION
            return "foreign" if fault == "source" else "tesseract"
        if args[1] == "--version":
            return "tesseract 5.5.0" if fault == "cli" else "tesseract 5.5.4"
        assert kwargs["env"]["OMP_THREAD_LIMIT"] == "1"
        assert "TESSDATA_PREFIX" not in kwargs["env"]
        if args[1] == "--list-langs":
            return "eng\nosd" if fault == "language" else "eng\nkor\nosd"
        language = args[args.index("-l") + 1]
        return "wrong" if fault == language else {"eng": "ACADEMY 12345", "kor": "한글 시험"}[language]

    monkeypatch.setenv("TESSDATA_PREFIX", "untrusted-model-override")
    monkeypatch.setattr(verifier.subprocess, "check_output", read)
    monkeypatch.setattr(verifier.ctypes, "CDLL", lambda name: name == "libtesseract.so.5")
    monkeypatch.setattr(verifier.ImageFont, "truetype", lambda *args: None)
    monkeypatch.setattr(verifier.ImageDraw, "Draw", lambda image: type("Draw", (), {"text": lambda *args, **kwargs: None})())
    if fault:
        with pytest.raises(RuntimeError, match="TESSERACT_"):
            verifier.verify()
        assert "_OK" not in capsys.readouterr().out
    else:
        verifier.verify()
        assert "TESSERACT_5_5_4_PACKAGE_ABI_ENG_KOR_OK" in capsys.readouterr().out
        assert len([args for args in calls if "--psm" in args]) == 2


def test_trixie_source_build_keeps_signature_and_dependency_generation():
    source = (ROOT / "docker/native-security/build-tesseract.sh").read_text(encoding="utf-8")
    assert "gpgv --keyring /usr/share/keyrings/debian-keyring.gpg" in source
    assert source.index("gpgv --keyring") < source.index("dpkg-source -x")
    assert source.count("download tesseract_") == 4
    assert "dpkg-buildpackage --build=binary --unsigned-source --unsigned-changes" in source
    assert "nocheck" not in source
    assert 'dpkg-deb -f "$1" Source' in source


@pytest.mark.parametrize("image", ["api", "ai-worker-cpu", "tools-worker"])
def test_each_ocr_consumer_installs_both_fixed_packages_and_runs_actual_smoke(image):
    source = (ROOT / "docker" / image / "Dockerfile").read_text(encoding="utf-8")
    assert "/usr/local/share/academy/tesseract/tesseract-ocr.deb" in source
    assert "/usr/local/share/academy/tesseract/libtesseract5.deb" in source
    assert "tesseract-ocr-kor" in source
    assert source.index("RUN pip install") < source.index("RUN python /usr/local/bin/verify-tesseract.py")
    assert "forky" not in source
