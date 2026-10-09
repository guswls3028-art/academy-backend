"""Verify the installed CUPS TLS implementation and real headless conversion."""

import argparse
import base64
import ctypes
from pathlib import Path
import subprocess
import tempfile
import zipfile


def verify():
    package = subprocess.check_output(["dpkg-query", "-W", "-f=${Version}\n${Depends}", "libcups2t64"], text=True)
    version, dependencies = package.split("\n", 1)
    if version != "2.4.10-3+deb13u2+academy1" or "libssl3t64" not in dependencies or "gnutls" in dependencies:
        raise RuntimeError("Unexpected CUPS package or TLS dependency")
    # Verify the physical library as well as dpkg: metadata removal is not a fix.
    if any(Path("/usr/lib").glob("*/libgnutls.so*")) or any(Path("/lib").glob("*/libgnutls.so*")):
        raise RuntimeError("Unexpected GnuTLS runtime library")
    library = ctypes.CDLL("libcups.so.2")
    options = ctypes.c_void_p()
    library.cupsParseOptions.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    library.cupsParseOptions.restype = ctypes.c_int
    library.cupsGetOption.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
    library.cupsGetOption.restype = ctypes.c_char_p
    library.cupsFreeOptions.argtypes = [ctypes.c_int, ctypes.c_void_p]
    library.cupsFreeOptions.restype = None
    count = library.cupsParseOptions(b"media=A4 sides=two-sided-long-edge", 0, ctypes.byref(options))
    try:
        if count != 2 or library.cupsGetOption(b"media", count, options) != b"A4":
            raise RuntimeError("CUPS option parsing failed")
        if library.cupsGetOption(b"not-present", count, options) is not None:
            raise RuntimeError("CUPS missing option did not fail closed")
    finally:
        library.cupsFreeOptions(count, options)
    loaded = Path("/proc/self/maps").read_text()
    if "libssl.so.3" not in loaded or "libcrypto.so.3" not in loaded or "libgnutls.so" in loaded:
        raise RuntimeError("Unexpected loaded CUPS TLS implementation")
    print("CUPS_OPENSSL_OPTIONS_PASS")


def verify_office():
    import fitz

    with tempfile.TemporaryDirectory(prefix="cups-office-verify-") as temporary:
        root = Path(temporary)
        source = root / "sample.docx"
        with zipfile.ZipFile(source, "w") as package:
            package.writestr(
                "[Content_Types].xml",
                '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
            )
            package.writestr(
                "_rels/.rels",
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="doc" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
            )
            package.writestr(
                "word/document.xml",
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>Academy document conversion</w:t></w:r></w:p><w:p><w:r><w:t>수업 분석 자료</w:t></w:r></w:p></w:body></w:document>',
            )
        subprocess.run(
            [
                "resource-office",
                f"-env:UserInstallation={(root / 'profile').as_uri()}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(root),
                str(source),
            ],
            check=True,
            timeout=90,
            capture_output=True,
        )
        pdf_path = root / "sample.pdf"
        with fitz.open(pdf_path) as document:
            text = "".join(page.get_text() for page in document)
            if document.page_count != 1 or "Academy document conversion" not in text or "수업 분석 자료" not in text:
                # This document is generated above from fixed synthetic text.
                # Keep the actual output available when an image build fails,
                # so rendering and extraction defects can be distinguished.
                print(f"CUPS_OFFICE_CONTENT: pages={document.page_count} text={text!r}", flush=True)
                if pdf_path.is_file():
                    print(
                        "CUPS_OFFICE_PDF_BASE64=" + base64.b64encode(pdf_path.read_bytes()).decode("ascii"), flush=True
                    )
                raise RuntimeError("Office conversion lost the document content")
    print("CUPS_OPENSSL_OFFICE_PDF_PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--office", action="store_true")
    arguments = parser.parse_args()
    verify()
    if arguments.office:
        verify_office()
