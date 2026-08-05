"""Legacy HWP endnote visual extraction without converting user documents.

Ymath's teacher-authored source keeps each numbered problem and handwritten
solution in a Hangul endnote picture.  The extractor deliberately reads only
that stable OLE/record boundary and never attempts to rewrite HWP content.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
from pathlib import PurePosixPath
import struct
from xml.etree import ElementTree
import zlib
from zipfile import ZipFile

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

_CTRL_HEADER_TAG = 71
_SHAPE_PICTURE_TAG = 85
_ENDNOTE_CHID = b"  ne"
_MAX_NOTES = 200
_MAX_PICTURES = 500
_MAX_IMAGE_PIXELS = 40_000_000
_MAX_DECOMPRESSED_IMAGE_BYTES = 160 * 1024 * 1024
_MAX_HWPX_ENTRIES = 5_000
_MAX_HWPX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True)
class HwpEndnoteVisual:
    number: int
    png_bytes: bytes
    width: int
    height: int
    picture_count: int


@dataclass(frozen=True)
class HwpEndnoteExtraction:
    control_numbers: tuple[int, ...]
    visuals: tuple[HwpEndnoteVisual, ...]

    @property
    def missing_visual_numbers(self) -> tuple[int, ...]:
        visual_numbers = {visual.number for visual in self.visuals}
        return tuple(
            number for number in self.control_numbers if number not in visual_numbers
        )


def _iter_records(data: bytes):
    offset = 0
    length = len(data)
    while offset + 4 <= length:
        header = struct.unpack_from("<I", data, offset)[0]
        offset += 4
        tag = header & 0x3FF
        level = (header >> 10) & 0x3FF
        size = (header >> 20) & 0xFFF
        if size == 0xFFF:
            if offset + 4 > length:
                return
            size = struct.unpack_from("<I", data, offset)[0]
            offset += 4
        if size < 0 or offset + size > length:
            return
        payload = data[offset : offset + size]
        offset += size
        yield tag, level, payload


def _collect_endnote_picture_ids(records) -> list[tuple[int, list[int]]]:
    """Collect ``(endnote number, BinData ids)`` from decoded HWP records."""
    notes: list[tuple[int, list[int]]] = []
    current_number: int | None = None
    current_level: int | None = None
    current_picture_ids: list[int] = []
    total_picture_count = 0

    def finish() -> None:
        nonlocal current_number, current_level, current_picture_ids, total_picture_count
        remaining = max(_MAX_PICTURES - total_picture_count, 0)
        accepted = current_picture_ids[:remaining]
        if current_number and accepted and len(notes) < _MAX_NOTES:
            notes.append((current_number, accepted))
            total_picture_count += len(accepted)
        current_number = None
        current_level = None
        current_picture_ids = []

    for tag, level, payload in records:
        if current_number is not None and current_level is not None and level <= current_level:
            finish()
        if tag == _CTRL_HEADER_TAG and len(payload) >= 8 and payload[:4] == _ENDNOTE_CHID:
            finish()
            number = struct.unpack_from("<I", payload, 4)[0]
            if 0 < number <= 999:
                current_number = int(number)
                current_level = int(level)
            continue
        if current_number is not None and tag == _SHAPE_PICTURE_TAG and len(payload) >= 73:
            bindata_id = struct.unpack_from("<H", payload, 71)[0]
            if (
                bindata_id
                and bindata_id not in current_picture_ids
                and total_picture_count + len(current_picture_ids) < _MAX_PICTURES
            ):
                current_picture_ids.append(int(bindata_id))
    finish()
    return notes


def _collect_endnote_numbers(records) -> list[int]:
    numbers: list[int] = []
    for tag, _level, payload in records:
        if tag != _CTRL_HEADER_TAG or len(payload) < 8 or payload[:4] != _ENDNOTE_CHID:
            continue
        number = struct.unpack_from("<I", payload, 4)[0]
        if 0 < number <= 999 and number not in numbers:
            numbers.append(int(number))
    return numbers


def _read_body_sections(ole) -> list[bytes]:
    header = ole.openstream("FileHeader").read()
    compressed = len(header) >= 40 and bool(struct.unpack_from("<I", header, 36)[0] & 1)
    section_names = sorted(
        (
            "/".join(parts)
            for parts in ole.listdir(streams=True, storages=False)
            if len(parts) == 2 and parts[0] == "BodyText" and parts[1].startswith("Section")
        ),
        key=lambda value: int(value.rsplit("Section", 1)[1]),
    )
    sections: list[bytes] = []
    for name in section_names:
        data = ole.openstream(name).read()
        if compressed:
            data = zlib.decompress(data, -15)
        sections.append(data)
    return sections


def _bindata_streams_by_id(ole) -> dict[int, str]:
    streams: dict[int, str] = {}
    for parts in ole.listdir(streams=True, storages=False):
        if len(parts) != 2 or parts[0] != "BinData":
            continue
        stem = parts[1].split(".", 1)[0]
        if not stem.upper().startswith("BIN"):
            continue
        try:
            streams[int(stem[3:], 16)] = "/".join(parts)
        except ValueError:
            continue
    return streams


def _inflate_picture(raw: bytes) -> bytes | None:
    try:
        inflater = zlib.decompressobj(-15)
        decoded = inflater.decompress(raw, _MAX_DECOMPRESSED_IMAGE_BYTES + 1)
        if len(decoded) > _MAX_DECOMPRESSED_IMAGE_BYTES or not inflater.eof:
            return None
        return decoded
    except zlib.error:
        return None


def _load_picture(raw: bytes) -> Image.Image | None:
    candidates = (raw, _inflate_picture(raw))
    for candidate in candidates:
        if not candidate:
            continue
        try:
            with Image.open(BytesIO(candidate)) as source:
                if source.width * source.height > _MAX_IMAGE_PIXELS:
                    return None
                return ImageOps.exif_transpose(source).convert("RGB")
        except Exception:
            continue
    return None


def _stack_as_png(images: list[Image.Image]) -> tuple[bytes, int, int]:
    width = max(image.width for image in images)
    height = sum(image.height for image in images)
    if width * height > _MAX_IMAGE_PIXELS:
        raise ValueError("HWP 해설 이미지가 안전 처리 한도를 초과했습니다.")
    canvas = Image.new("RGB", (width, height), "white")
    y = 0
    for image in images:
        canvas.paste(image, ((width - image.width) // 2, y))
        y += image.height
    output = BytesIO()
    # Teacher handwriting images are large and plentiful. Low compression keeps
    # worker latency bounded; R2/CDN compression is not worth holding the job.
    canvas.save(output, format="PNG", compress_level=2)
    data = output.getvalue()
    canvas.close()
    return data, width, height


def extract_hwp_endnotes(path: str) -> HwpEndnoteExtraction:
    """Return legacy HWP endnote coverage and source visuals."""
    import olefile

    if not olefile.isOleFile(path):
        raise ValueError("지원되는 HWP 5.x 문서가 아닙니다.")

    with olefile.OleFileIO(path) as ole:
        notes: list[tuple[int, list[int]]] = []
        control_numbers: list[int] = []
        for section in _read_body_sections(ole):
            records = list(_iter_records(section))
            notes.extend(_collect_endnote_picture_ids(iter(records)))
            for number in _collect_endnote_numbers(iter(records)):
                if number not in control_numbers:
                    control_numbers.append(number)
        streams = _bindata_streams_by_id(ole)
        visuals: list[HwpEndnoteVisual] = []
        seen_numbers: set[int] = set()
        for number, picture_ids in notes:
            if number in seen_numbers:
                logger.warning("HWP_ENDNOTE_DUPLICATE_NUMBER | number=%s", number)
                continue
            images = []
            for picture_id in picture_ids:
                stream_name = streams.get(picture_id)
                if not stream_name:
                    continue
                image = _load_picture(ole.openstream(stream_name).read())
                if image is not None:
                    images.append(image)
            if not images:
                continue
            try:
                png_bytes, width, height = _stack_as_png(images)
            finally:
                for image in images:
                    image.close()
            visuals.append(
                HwpEndnoteVisual(
                    number=number,
                    png_bytes=png_bytes,
                    width=width,
                    height=height,
                    picture_count=len(images),
                )
            )
            seen_numbers.add(number)
    return HwpEndnoteExtraction(
        control_numbers=tuple(control_numbers),
        visuals=tuple(sorted(visuals, key=lambda item: item.number)),
    )


def extract_hwp_endnote_visuals(path: str) -> list[HwpEndnoteVisual]:
    """Return teacher-authored legacy HWP endnote visuals by note number."""
    return list(extract_hwp_endnotes(path).visuals)


def _safe_hwpx_entries(archive: ZipFile):
    entries = archive.infolist()
    if len(entries) > _MAX_HWPX_ENTRIES:
        raise ValueError("HWPX 항목 수가 안전 처리 한도를 초과했습니다.")
    if sum(max(0, entry.file_size) for entry in entries) > _MAX_HWPX_UNCOMPRESSED_BYTES:
        raise ValueError("HWPX 압축 해제 크기가 안전 처리 한도를 초과했습니다.")
    for entry in entries:
        path = PurePosixPath(entry.filename)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("안전하지 않은 HWPX 경로가 포함되어 있습니다.")
    return entries


def extract_hwpx_endnotes(path: str) -> HwpEndnoteExtraction:
    """Return HWPX endnote visuals without rewriting the source document."""
    with ZipFile(path) as archive:
        entries = _safe_hwpx_entries(archive)
        names = {entry.filename for entry in entries}
        image_members = {
            PurePosixPath(name).stem.lower(): name
            for name in names
            if PurePosixPath(name).parts[:1] == ("BinData",)
        }
        section_names = sorted(
            (
                name
                for name in names
                if PurePosixPath(name).parts[:1] == ("Contents",)
                and PurePosixPath(name).name.lower().startswith("section")
                and PurePosixPath(name).suffix.lower() == ".xml"
            ),
            key=lambda name: int(
                "".join(character for character in PurePosixPath(name).stem if character.isdigit())
                or "0"
            ),
        )
        control_numbers: list[int] = []
        visuals: list[HwpEndnoteVisual] = []
        seen_numbers: set[int] = set()
        total_picture_count = 0

        for section_name in section_names:
            try:
                root = ElementTree.fromstring(archive.read(section_name))
            except ElementTree.ParseError:
                continue
            for endnote in root.iter():
                if endnote.tag.rsplit("}", 1)[-1] != "endNote":
                    continue
                raw_number = str(endnote.attrib.get("number") or "")
                if not raw_number.isdigit():
                    continue
                number = int(raw_number)
                if not 0 < number <= 999:
                    continue
                if number not in control_numbers:
                    control_numbers.append(number)
                if number in seen_numbers or len(visuals) >= _MAX_NOTES:
                    continue

                image_refs = []
                for element in endnote.iter():
                    if element.tag.rsplit("}", 1)[-1] != "img":
                        continue
                    image_ref = str(element.attrib.get("binaryItemIDRef") or "").strip()
                    if image_ref and image_ref.lower() not in image_refs:
                        image_refs.append(image_ref.lower())

                remaining = max(_MAX_PICTURES - total_picture_count, 0)
                images = []
                for image_ref in image_refs[:remaining]:
                    member_name = image_members.get(image_ref)
                    if not member_name:
                        continue
                    image = _load_picture(archive.read(member_name))
                    if image is not None:
                        images.append(image)
                if not images:
                    continue
                try:
                    png_bytes, width, height = _stack_as_png(images)
                finally:
                    for image in images:
                        image.close()
                visuals.append(
                    HwpEndnoteVisual(
                        number=number,
                        png_bytes=png_bytes,
                        width=width,
                        height=height,
                        picture_count=len(images),
                    )
                )
                total_picture_count += len(images)
                seen_numbers.add(number)

    return HwpEndnoteExtraction(
        control_numbers=tuple(control_numbers),
        visuals=tuple(sorted(visuals, key=lambda item: item.number)),
    )


def extract_document_endnotes(path: str, filename: str) -> HwpEndnoteExtraction:
    suffix = PurePosixPath(str(filename or "").lower()).suffix
    if suffix == ".hwp":
        return extract_hwp_endnotes(path)
    if suffix == ".hwpx":
        return extract_hwpx_endnotes(path)
    raise ValueError("HWP 또는 HWPX 파일이 필요합니다.")


def crop_problem_from_endnote(png_bytes: bytes, ratio: float = 0.3) -> bytes:
    """Crop the problem area from the top; the full visual remains the solution."""
    safe_ratio = min(max(float(ratio), 0.08), 0.98)
    with Image.open(BytesIO(png_bytes)) as source:
        image = source.convert("RGB")
        bottom = max(1, round(image.height * safe_ratio))
        crop = image.crop((0, 0, image.width, bottom))
        output = BytesIO()
        crop.save(output, format="PNG", compress_level=2)
        return output.getvalue()
