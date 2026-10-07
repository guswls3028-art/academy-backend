#!/usr/bin/env python3
"""Verify the installed TIFF package ABI, codecs and real image decoding."""
from __future__ import annotations

import ctypes
import json
import subprocess
from pathlib import Path


def verify_tiff() -> dict[str, object]:
    for package in ("libtiff6", "libtiffxx6"):
        version = subprocess.check_output(
            ["dpkg-query", "-W", "-f=${Version}", package], text=True,
        ).strip()
        source = subprocess.check_output(
            ["dpkg-query", "-W", "-f=${Source}", package], text=True,
        ).strip()
        if version != "4.7.2-1+academy1" or source != "tiff":
            raise RuntimeError(f"Unexpected TIFF package identity: {package} {source} {version}")

    library = ctypes.CDLL("libtiff.so.6")
    ctypes.CDLL("libtiffxx.so.6")
    library.TIFFGetVersion.restype = ctypes.c_char_p
    version = library.TIFFGetVersion().decode().splitlines()[0]
    if version != "LIBTIFF, Version 4.7.2":
        raise RuntimeError(f"Unexpected loaded TIFF version: {version}")
    library.TIFFIsCODECConfigured.argtypes = [ctypes.c_uint16]
    library.TIFFIsCODECConfigured.restype = ctypes.c_int
    codecs = {
        "raw": 1, "rle": 2, "fax3": 3, "fax4": 4, "lzw": 5, "old-jpeg": 6,
        "jpeg": 7, "deflate": 8, "packbits": 32773, "jbig": 34661,
        "lerc": 34887, "lzma": 34925, "zstd": 50000, "webp": 50001,
    }
    for name, codec in codecs.items():
        if library.TIFFIsCODECConfigured(codec) != 1:
            raise RuntimeError(f"Required TIFF codec is missing: {name}")

    library.TIFFOpen.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    library.TIFFOpen.restype = ctypes.c_void_p
    library.TIFFClose.argtypes = [ctypes.c_void_p]
    library.TIFFClose.restype = None
    library.TIFFReadRGBAImageOriented.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_int, ctypes.c_int,
    ]
    library.TIFFReadRGBAImageOriented.restype = ctypes.c_int
    expected = (31, 92, 173)
    compressions = [
        ("raw", 0), ("tiff_lzw", 0), ("tiff_adobe_deflate", 0),
        ("jpeg", 5), ("lzma", 0), ("zstd", 0), ("packbits", 0),
    ]
    for compression, tolerance in compressions:
        path = Path("/usr/local/share/academy/native-security/tiff-fixtures") / f"{compression}.tiff"
        handle = library.TIFFOpen(str(path).encode(), b"r")
        if not handle:
            raise RuntimeError(f"System TIFF could not open {compression} fixture")
        try:
            pixels = (ctypes.c_uint32 * 256)()
            if library.TIFFReadRGBAImageOriented(handle, 16, 16, pixels, 1, 1) != 1:
                raise RuntimeError(f"System TIFF could not decode {compression} fixture")
            for pixel in pixels:
                actual = (pixel & 255, (pixel >> 8) & 255, (pixel >> 16) & 255)
                if (pixel >> 24) != 255 or any(
                    abs(left - right) > tolerance for left, right in zip(actual, expected)
                ):
                    raise RuntimeError(f"TIFF {compression} pixel mismatch: {actual}")
        finally:
            library.TIFFClose(handle)
    return {"library": version, "codecs": list(codecs), "decoded": len(compressions)}


if __name__ == "__main__":
    print(json.dumps(verify_tiff(), sort_keys=True))
