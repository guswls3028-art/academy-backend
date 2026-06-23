import io
import re
import struct
import unicodedata
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree

from apps.domains.tools.problem_studio.structure import normalize_space


DEFAULT_MAX_ZIP_MEMBERS = 400
DEFAULT_MAX_ZIP_UNCOMPRESSED_BYTES = 180 * 1024 * 1024


def safe_zip_members(
    zf: zipfile.ZipFile,
    *,
    max_members: int = DEFAULT_MAX_ZIP_MEMBERS,
    max_uncompressed_bytes: int = DEFAULT_MAX_ZIP_UNCOMPRESSED_BYTES,
) -> list[zipfile.ZipInfo]:
    members = zf.infolist()
    if len(members) > max_members:
        raise ValueError("문서 내부 파일 수가 너무 많습니다.")
    total = sum(max(0, int(member.file_size or 0)) for member in members)
    if total > max_uncompressed_bytes:
        raise ValueError("문서 내부 용량이 너무 큽니다.")
    return members


def xml_text(xml_bytes: bytes) -> str:
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return ""
    texts: list[str] = []
    for elem in root.iter():
        if elem.text and elem.text.strip():
            tag = elem.tag.rsplit("}", 1)[-1]
            if tag in {"t", "tab", "lineBreak", "p"}:
                texts.append(elem.text.strip())
    return " ".join(texts)


def extract_pdf_text(data: bytes) -> str:
    try:
        from academy.adapters.tools.pymupdf_renderer import extract_pdf_text_from_bytes
    except Exception as exc:  # pragma: no cover - dependency is present in api image
        raise ValueError("PDF 텍스트 추출 모듈을 사용할 수 없습니다.") from exc

    return normalize_space(extract_pdf_text_from_bytes(data))


def extract_hwpx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = safe_zip_members(zf)
        names = [member.filename for member in members]
        if "Preview/PrvText.txt" in names:
            return normalize_space(zf.read("Preview/PrvText.txt").decode("utf-8", "ignore"))
        section_names = sorted(
            name for name in names
            if name.startswith("Contents/") and name.lower().endswith(".xml")
        )
        chunks = [xml_text(zf.read(name)) for name in section_names]
    return normalize_space("\n".join(chunks))


def extract_docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        safe_zip_members(zf)
        if "word/document.xml" not in zf.namelist():
            return ""
        return normalize_space(xml_text(zf.read("word/document.xml")))


def _mime_for_suffix(suffix: str) -> str:
    suffix = suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".bmp":
        return "image/bmp"
    if suffix == ".gif":
        return "image/gif"
    return "application/octet-stream"


def _mime_from_magic(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _hwp_image_candidates(data: bytes) -> list[bytes]:
    candidates = [data]
    for wbits in (-15, zlib.MAX_WBITS):
        try:
            decompressed = zlib.decompress(data, wbits)
        except Exception:
            continue
        if decompressed and decompressed not in candidates:
            candidates.append(decompressed)
    return candidates


def normalize_hwp_image_data(filename: str, data: bytes) -> tuple[str, bytes]:
    """Return browser/Office-safe image bytes for a HWP BinData stream."""
    suffix_mime = _mime_for_suffix(Path(filename).suffix)
    for candidate in _hwp_image_candidates(data):
        magic_mime = _mime_from_magic(candidate)
        if magic_mime and magic_mime != "image/bmp":
            return magic_mime, candidate

        try:
            from PIL import Image

            with Image.open(io.BytesIO(candidate)) as image:
                fmt = (image.format or "").upper()
                if fmt in {"JPEG", "PNG", "GIF", "WEBP"} and magic_mime != "image/bmp":
                    return {
                        "JPEG": "image/jpeg",
                        "PNG": "image/png",
                        "GIF": "image/gif",
                        "WEBP": "image/webp",
                    }[fmt], candidate

                if image.mode not in {"RGB", "RGBA", "L", "P"}:
                    image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                out = io.BytesIO()
                image.save(out, format="PNG", optimize=True)
                return "image/png", out.getvalue()
        except Exception:
            continue

    return suffix_mime, data


def _iter_hwp_records(data: bytes):
    pos = 0
    size = len(data)
    while pos + 4 <= size:
        header = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        tag = header & 0x3ff
        level = (header >> 10) & 0x3ff
        payload_size = (header >> 20) & 0xfff
        if payload_size == 0xfff:
            if pos + 4 > size:
                break
            payload_size = struct.unpack_from("<I", data, pos)[0]
            pos += 4
        payload = data[pos:pos + payload_size]
        pos += payload_size
        yield tag, level, payload


def _clean_hwp_text(text: str) -> str:
    chars: list[str] = []
    for ch in text:
        if ch in "\r\n\t":
            chars.append("\n" if ch == "\r" else ch)
            continue
        code = ord(ch)
        category = unicodedata.category(ch)
        if category[0] == "C" or 0xE000 <= code <= 0xF8FF or 0x4E00 <= code <= 0x9FFF:
            chars.append(" ")
        else:
            chars.append(ch)
    cleaned = "".join(chars)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_hwp_text_and_images(data: bytes, *, include_images: bool = True) -> tuple[str, list[tuple[str, str, bytes]]]:
    try:
        import olefile
    except Exception as exc:
        raise ValueError("HWP OLE 분석 모듈을 사용할 수 없습니다.") from exc

    text_chunks: list[str] = []
    images: list[tuple[str, str, bytes]] = []
    with olefile.OleFileIO(io.BytesIO(data)) as ole:
        header = ole.openstream(["FileHeader"]).read()
        flags = struct.unpack_from("<I", header, 36)[0]
        compressed = bool(flags & 1)
        streams = ole.listdir(streams=True, storages=False)

        section_names = sorted(
            [parts for parts in streams if len(parts) >= 2 and parts[0] == "BodyText" and parts[1].startswith("Section")],
            key=lambda parts: int(re.sub(r"\D+", "", parts[1]) or "0"),
        )
        for parts in section_names:
            section_data = ole.openstream(parts).read()
            if compressed:
                try:
                    section_data = zlib.decompress(section_data, -15)
                except Exception:
                    section_data = zlib.decompress(section_data)
            for tag, _level, payload in _iter_hwp_records(section_data):
                if tag != 67:
                    continue
                text = _clean_hwp_text(payload.decode("utf-16le", "ignore"))
                if text:
                    text_chunks.append(text)

        if include_images:
            for parts in streams:
                if len(parts) < 2 or parts[0] != "BinData":
                    continue
                filename = parts[-1]
                suffix = Path(filename).suffix.lower()
                if suffix not in {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}:
                    continue
                image_data = ole.openstream(parts).read()
                mime, normalized = normalize_hwp_image_data(filename, image_data)
                images.append((filename, mime, normalized))

    return _clean_hwp_text("\n\n".join(text_chunks)), images


def extract_hwp_text(data: bytes) -> str:
    text, _images = extract_hwp_text_and_images(data, include_images=False)
    return text
