"""Exercise the actual system Expat and CPython XML entry points without I/O."""

import argparse
import ctypes
from pathlib import Path
import subprocess
import sys
import sysconfig


def native_parser(library):
    lib = ctypes.CDLL(library)
    lib.XML_ParserCreate.argtypes = [ctypes.c_char_p]
    lib.XML_ParserCreate.restype = ctypes.c_void_p
    lib.XML_Parse.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    lib.XML_Parse.restype = ctypes.c_int
    lib.XML_ParserFree.argtypes = [ctypes.c_void_p]
    lib.XML_ExpatVersion.restype = ctypes.c_char_p
    assert lib.XML_ExpatVersion() == b"expat_2.8.4"

    def parse(data):
        parser = lib.XML_ParserCreate(None)
        assert parser
        try:
            return lib.XML_Parse(parser, data, len(data), 1) == 1
        finally:
            lib.XML_ParserFree(parser)

    return parse


def python_parsers(module_dir):
    import pyexpat
    import _elementtree
    import xml.etree.ElementTree as etree

    assert sys.version_info[:3] == (3, 11, 15)
    assert pyexpat.EXPAT_VERSION == "expat_2.8.4"
    suffix = sysconfig.get_config_var("EXT_SUFFIX")
    for module in [pyexpat, _elementtree]:
        assert Path(module.__file__).resolve() == (module_dir / f"{module.__name__}{suffix}").resolve()
    # Importing the C accelerator checks all three Expat CAPI version fields.
    assert etree.XMLParser is _elementtree.XMLParser

    def expat_parse(data):
        try:
            pyexpat.ParserCreate().Parse(data, True)
            return True
        except pyexpat.ExpatError:
            return False

    def etree_parse(data):
        try:
            etree.fromstring(data)
            return True
        except etree.ParseError:
            return False

    return [expat_parse, etree_parse]


def verify_wide_output(library):
    lib = ctypes.CDLL(library)
    lib.XML_ParserCreate.argtypes = [ctypes.c_void_p]
    lib.XML_ParserCreate.restype = ctypes.c_void_p
    lib.XML_Parse.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    lib.XML_Parse.restype = ctypes.c_int
    lib.XML_ParserFree.argtypes = [ctypes.c_void_p]
    callback_type = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int)
    lib.XML_SetCharacterDataHandler.argtypes = [ctypes.c_void_p, callback_type]
    chunks = []

    @callback_type
    def collect(_user_data, characters, length):
        chunks.append(ctypes.string_at(characters, length * 2))

    parser = lib.XML_ParserCreate(None)
    assert parser
    text = "한국어 😀\U00010000\U0010ffff"
    data = f"<a>{text}</a>".encode("utf-8")
    try:
        lib.XML_SetCharacterDataHandler(parser, collect)
        assert lib.XML_Parse(parser, data, len(data), 1) == 1
        encoding = "utf-16-le" if sys.byteorder == "little" else "utf-16-be"
        assert b"".join(chunks) == text.encode(encoding), "EXPAT_WIDE_OUTPUT_ABI_MISMATCH"
    finally:
        lib.XML_ParserFree(parser)


def verify(parsers):
    accepted_invalid = 0
    for parse in parsers:
        for encoding, bom in [("utf-16-le", b"\xff\xfe"), ("utf-16-be", b"\xfe\xff")]:
            for text in ["<a>academy</a>", "<a>한국어 😀</a>", "<a>\U00010000\U0010ffff</a>"]:
                assert parse(bom + text.encode(encoding)), "EXPAT_VALID_XML_REJECTED"
            for text in ["<a>\ud800A</a>", "<a>\udbff\ue000</a>", "<a>\ud800\ud800</a>"]:
                if parse(bom + text.encode(encoding, errors="surrogatepass")):
                    accepted_invalid += 1
    if accepted_invalid:
        # The unpatched source must fail this same oracle before the fix is applied.
        print("EXPAT_UTF16_INVALID_ACCEPTED")
        return 66
    print("EXPAT_UTF16_VALID_AND_INVALID_PASS")
    return 0


def verify_package():
    for field, expected in [("Version", "2.8.4+academy1-1"), ("Source", "expat")]:
        actual = subprocess.check_output(["dpkg-query", "-W", f"-f=${{{field}}}", "libexpat1"], text=True)
        assert actual == expected
    ctypes.CDLL("libexpatw.so.1")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", required=True)
    parser.add_argument("--python", action="store_true")
    parser.add_argument("--module-dir", type=Path, default=Path(sysconfig.get_config_var("DESTSHARED") or "."))
    parser.add_argument("--package", action="store_true")
    parser.add_argument("--wide", action="store_true")
    args = parser.parse_args()
    if args.package:
        verify_package()
    parsers = [native_parser(args.library)]
    if args.wide:
        verify_wide_output(args.library)
    if args.package:
        verify_wide_output("libexpatw.so.1")
        parsers.append(native_parser("libexpatw.so.1"))
    if args.python:
        parsers.extend(python_parsers(args.module_dir))
    sys.exit(verify(parsers))
