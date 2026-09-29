"""Check the native-build oracle and the unchanged fail-closed package boundary."""

import importlib.util
import ctypes
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "verify_expat", ROOT / "docker/native-security/verify-expat.py"
)
assert SPEC and SPEC.loader
verifier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verifier)


def test_xml_oracle_accepts_valid_unicode_and_rejects_invalid_surrogates(capsys):
    seen = []

    def parse(data):
        seen.append(data)
        try:
            data.decode("utf-16")
        except UnicodeDecodeError:
            return False
        return True

    assert verifier.verify([parse]) == 0
    assert len(seen) == 12
    assert {data[:2] for data in seen} == {b"\xff\xfe", b"\xfe\xff"}
    assert capsys.readouterr().out == "EXPAT_UTF16_VALID_AND_INVALID_PASS\n"


def test_xml_oracle_detects_unpatched_acceptance_without_printing_payload(capsys):
    assert verifier.verify([lambda data: True]) == 66
    assert capsys.readouterr().out == "EXPAT_UTF16_INVALID_ACCEPTED\n"


def test_xml_oracle_rejects_broken_valid_xml_parser():
    with pytest.raises(AssertionError, match="EXPAT_VALID_XML_REJECTED"):
        verifier.verify([lambda data: False])


@pytest.mark.parametrize("valid_abi", [True, False])
def test_wide_output_oracle_requires_utf16_callback_bytes(monkeypatch, valid_abi):
    class Library:
        pass

    library = Library()
    callbacks, freed = [], []
    library.XML_ParserCreate = lambda encoding: 1
    library.XML_ParserFree = lambda parser: freed.append(parser)
    library.XML_SetCharacterDataHandler = lambda parser, callback: callbacks.append(callback)

    def parse(parser, data, length, final):
        assert parser == 1 and length == len(data) and final == 1
        text = data.decode("utf-8")[3:-4]
        endian = "le" if verifier.sys.byteorder == "little" else "be"
        encoded = text.encode(f"utf-16-{endian}" if valid_abi else f"utf-32-{endian}")
        buffer = ctypes.create_string_buffer(encoded)
        callbacks[0](None, ctypes.addressof(buffer), len(encoded) // 2)
        return 1

    library.XML_Parse = parse
    monkeypatch.setattr(verifier.ctypes, "CDLL", lambda path: library)
    if valid_abi:
        verifier.verify_wide_output("test-library")
    else:
        with pytest.raises(AssertionError, match="EXPAT_WIDE_OUTPUT_ABI_MISMATCH"):
            verifier.verify_wide_output("test-library")
    assert freed == [1]


@pytest.mark.parametrize(
    ("version", "source", "valid"),
    [
        ("2.8.4+academy1-1", "expat", True),
        ("2.8.3-1~deb13u1", "expat", False),
        ("2.8.5", "expat", False),
        ("2.8.4+academy1-1", "academy-expat", False),
    ],
)
def test_package_verifier_requires_exact_backport_and_honest_source(
    monkeypatch, version, source, valid
):
    queries, libraries = [], []

    def query(argv, *, text):
        assert text and argv[:2] == ["dpkg-query", "-W"] and argv[-1] == "libexpat1"
        queries.append(argv[2])
        return {"-f=${Version}": version, "-f=${Source}": source}[argv[2]]

    monkeypatch.setattr(verifier.subprocess, "check_output", query)
    monkeypatch.setattr(verifier.ctypes, "CDLL", libraries.append)
    if valid:
        verifier.verify_package()
        assert queries == ["-f=${Version}", "-f=${Source}"]
        assert libraries == ["libexpatw.so.1"]
    else:
        with pytest.raises(AssertionError):
            verifier.verify_package()
        assert libraries == []


def test_apt_consumers_reverify_the_fixed_package_and_python_pair():
    invocation = "python /usr/local/bin/verify-expat.py --library libexpat.so.1 --python --package"
    for path in [
        "docker/Dockerfile.base", "docker/api/Dockerfile", "docker/ai-worker-cpu/Dockerfile",
        "docker/tools-worker/Dockerfile", "docker/video-worker/Dockerfile",
    ]:
        dockerfile = (ROOT / path).read_text(encoding="utf-8")
        assert dockerfile.rindex(invocation) > dockerfile.rindex("apt-get install"), path
