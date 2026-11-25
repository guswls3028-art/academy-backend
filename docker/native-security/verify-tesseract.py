"""Benign installed OCR smoke only: trusted system data, no malformed models."""
import ctypes
import os
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageFont

VERSION = "5.5.4-1+academy1"


def verify():
    for package in ("tesseract-ocr", "libtesseract5"):
        version = subprocess.check_output(
            ["dpkg-query", "-W", "-f=${Version}", package], text=True, timeout=10,
        ).strip()
        if version != VERSION:
            raise RuntimeError("TESSERACT_PACKAGE_VERSION")
        source = subprocess.check_output(
            ["dpkg-query", "-W", "-f=${source:Package}", package], text=True, timeout=10,
        ).strip()
        if source != "tesseract":
            raise RuntimeError("TESSERACT_SOURCE_IDENTITY")
    cli_version = subprocess.check_output(
        ["tesseract", "--version"], text=True, timeout=10,
        stderr=subprocess.STDOUT,
    ).splitlines()[0]
    if cli_version.strip() != "tesseract 5.5.4":
        raise RuntimeError("TESSERACT_CLI_VERSION")
    ctypes.CDLL("libtesseract.so.5")
    environment = {**os.environ, "OMP_THREAD_LIMIT": "1"}
    environment.pop("TESSDATA_PREFIX", None)
    languages = subprocess.check_output(
        ["tesseract", "--list-langs"], text=True, timeout=10, env=environment,
    ).splitlines()
    if not {"eng", "kor", "osd"}.issubset(languages):
        raise RuntimeError("TESSERACT_SYSTEM_LANGUAGES")
    font = ImageFont.truetype("/usr/share/fonts/truetype/nanum/NanumGothic.ttf", 72)
    with tempfile.TemporaryDirectory(prefix="academy-ocr-smoke-") as directory:
        for language, text in (("eng", "ACADEMY 12345"), ("kor", "한글 시험")):
            image = Image.new("RGB", (1000, 150), "white")
            ImageDraw.Draw(image).text((35, 25), text, font=font, fill="black")
            path = Path(directory) / (language + ".png")
            image.save(path)
            output = subprocess.check_output(
                ["tesseract", str(path), "stdout", "-l", language, "--psm", "7"],
                text=True, encoding="utf-8", timeout=30, env=environment,
                stderr=subprocess.DEVNULL,
            )
            if "".join(output.split()) != "".join(text.split()):
                raise RuntimeError("TESSERACT_BENIGN_OCR_" + language.upper())
    print("TESSERACT_5_5_4_PACKAGE_ABI_ENG_KOR_OK")


if __name__ == "__main__":
    verify()
