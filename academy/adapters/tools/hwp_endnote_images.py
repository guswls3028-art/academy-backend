"""HWP/HWPX body-question and endnote visual extraction.

Ymath's teacher-authored source links clean body problems to handwritten
solutions with numbered Hangul endnotes. The extractor uses that stable
document structure and never asks AI to infer the problem/solution boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
from pathlib import Path
from pathlib import PurePosixPath
import re
import struct
from xml.etree import ElementTree
import zlib
from zipfile import ZipFile

from PIL import Image, ImageDraw, ImageFont, ImageOps

logger = logging.getLogger(__name__)

_CTRL_HEADER_TAG = 71
_PARA_TEXT_TAG = 67
_SHAPE_PICTURE_TAG = 85
_EQEDIT_TAG = 88
_ENDNOTE_CHID = b"  ne"
_EQUATION_CHID = b"deqe"
_EQUATION_MARKER_START = "\ufff2"
_EQUATION_MARKER_END = "\ufff3"
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
    render_mode: str = "source_image"


@dataclass(frozen=True)
class HwpEndnoteExtraction:
    control_numbers: tuple[int, ...]
    visuals: tuple[HwpEndnoteVisual, ...]
    paired_visuals: tuple[HwpEndnoteVisual, ...] = ()
    problem_visuals: tuple[HwpEndnoteVisual, ...] = ()

    @property
    def missing_visual_numbers(self) -> tuple[int, ...]:
        visual_numbers = {visual.number for visual in self.visuals}
        return tuple(
            number for number in self.control_numbers if number not in visual_numbers
        )

    @property
    def missing_paired_visual_numbers(self) -> tuple[int, ...]:
        visual_numbers = {visual.number for visual in self.paired_visuals}
        return tuple(
            number for number in self.control_numbers if number not in visual_numbers
        )

    @property
    def missing_problem_visual_numbers(self) -> tuple[int, ...]:
        visual_numbers = {visual.number for visual in self.problem_visuals}
        return tuple(
            number for number in self.control_numbers if number not in visual_numbers
        )


@dataclass(frozen=True)
class _HwpEndnoteContent:
    number: int
    paragraphs: tuple[str, ...]
    equation_count: int
    picture_refs: tuple[int | str, ...] = ()


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


def _decode_hwp_equation(payload: bytes) -> str:
    """Decode the documented EqEdit script stored after the record prefix."""
    if len(payload) < 6:
        return ""
    character_count = struct.unpack_from("<H", payload, 4)[0]
    byte_count = min(character_count * 2, max(len(payload) - 6, 0))
    value = payload[6 : 6 + byte_count].decode("utf-16le", "ignore").strip()
    # Some Hangul builds append an internal text-object sentinel to EqEdit's
    # stored script. It is not authored math and must never reach the preview.
    return re.sub(r"\n+To\n+\d+\s*$", "", value).strip()


def _decode_hwp_paragraph_template(payload: bytes) -> tuple[str, int]:
    """Decode ParaText and retain stable slots for native equation controls."""
    source = payload.decode("utf-16le", "ignore")
    output: list[str] = []
    equation_slots = 0
    index = 0
    while index < len(source):
        character = source[index]
        code = ord(character)
        if 0 < code < 0x20:
            is_inline = index + 7 < len(source) and source[index + 7] == character
            if is_inline:
                raw_control = source[index : index + 8].encode(
                    "utf-16le", "surrogatepass"
                )
                control_id = raw_control[2:6]
                if control_id == _EQUATION_CHID:
                    output.append(f"\ufff0{equation_slots}\ufff1")
                    equation_slots += 1
                elif code == 0x09:
                    output.append("\t")
                index += 8
                continue
            if code in {0x0A, 0x0D}:
                output.append("\n")
            elif code == 0x09:
                output.append("\t")
            index += 1
            continue
        output.append(character)
        index += 1
    value = "".join(output)
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip(), equation_slots


def _humanize_hwp_equation(script: str) -> str:
    """Make native HWP equation script readable in a review image.

    The original script remains the source of truth.  This intentionally uses
    conservative presentation substitutions rather than trying to reinterpret
    the mathematics with an AI model.
    """
    value = str(script or "").replace("`", " ")
    value = re.sub(
        r"(?<=[A-Za-z0-9)\]])\s*ge(?!q)\s*(?=[A-Za-z0-9(])",
        "≥",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"(?<=[A-Za-z0-9)\]])\s*ne(?!q)\s*(?=[A-Za-z0-9(])",
        "≠",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"((?<![A-Za-z])[A-Za-z0-9]|[)\]])\s*le(?!q)\s*"
        r"(?=(?:alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|phi|omega|"
        r"[A-Za-z](?![A-Za-z])))",
        r"\1≤",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"(?<=[A-Za-z0-9)\]])\s*leq\s*(?=[A-Za-z0-9(-])",
        "≤",
        value,
        flags=re.I,
    )
    value = re.sub(r"(?<![A-Za-z])it(?=[A-Za-z-])", "", value, flags=re.I)
    value = re.sub(r"(?<=[0-9])pi(?![A-Za-z])", "π", value, flags=re.I)
    value = re.sub(
        r"(?<![A-Za-z])(sin|cos|tan|log|ln)(?=[A-Za-z])",
        r"\1 ",
        value,
        flags=re.I,
    )

    value = re.sub(
        r"\broot\s*\{([^{}]+)\}\s*of\s*\{([^{}]+)\}",
        r"√[\1](\2)",
        value,
        flags=re.I,
    )

    fraction_pattern = re.compile(r"\{([^{}]+)\}\s+over\s+\{([^{}]+)\}", re.I)
    root_pattern = re.compile(r"sqrt\s*\{([^{}]+)\}", re.I)
    for _ in range(8):
        updated = fraction_pattern.sub(r"(\1)/(\2)", value)
        updated = root_pattern.sub(r"√(\1)", updated)
        if updated == value:
            break
        value = updated
    value = re.sub(
        r"(?<![A-Za-z0-9])([A-Za-z0-9]+)\s+over\s+([A-Za-z0-9]+)(?![A-Za-z0-9])",
        r"(\1)/(\2)",
        value,
        flags=re.I,
    )
    value = re.sub(r"\brm\s*\{([A-Za-z0-9]+)(?:\}|$)", r"\1", value)

    replacements = {
        "THEREFORE": "∴",
        "BECAUSE": "∵",
        "rarrow": "→",
        "rightarrow": "→",
        "larrow": "←",
        "INF": "∞",
        "GEQ": "≥",
        "LEQ": "≤",
        "NEQ": "≠",
        "TIMES": "×",
        "CDOT": "·",
        "DIVIDE": "÷",
        "PLUSMINUS": "±",
        "ANGLE": "∠",
        "DEG": "°",
        "NEARROW": "↗",
        "SEARROW": "↘",
        "CDOTS": "⋯",
        "LDOTS": "…",
        "BOT": "⊥",
        "CUP": "∪",
        "CAP": "∩",
        "alpha": "α",
        "beta": "β",
        "gamma": "γ",
        "delta": "δ",
        "theta": "θ",
        "lambda": "λ",
        "mu": "μ",
        "pi": "π",
        "sigma": "σ",
        "phi": "φ",
        "omega": "ω",
    }
    for token, replacement in replacements.items():
        value = re.sub(rf"\b{re.escape(token)}\b", replacement, value, flags=re.I)
    value = re.sub(r"\brm\s*\{([^{}]+)\}", r"\1", value, flags=re.I)
    value = re.sub(r"\brm\s*([A-Za-z]+)", r"\1", value, flags=re.I)
    value = re.sub(r"\brm\s*([0-9]+)", r"\1", value, flags=re.I)
    value = re.sub(
        r"(?<![A-Za-z])([A-Za-z])\s*prime(?![A-Za-z])",
        r"\1′",
        value,
        flags=re.I,
    )
    value = re.sub(r"(?<![A-Za-z])sqrt\s*([0-9]+)", r"√\1", value, flags=re.I)
    value = re.sub(r"(?<![A-Za-z])root\s*([0-9]+)", r"√\1", value, flags=re.I)
    value = re.sub(r"(?<![A-Za-z])pile\s*\{[^{}]*\}", "", value, flags=re.I)
    value = re.sub(r"\bcases\b", "", value, flags=re.I)
    value = re.sub(r"(?<![A-Za-z])LEFT(?![A-Za-z])", "", value, flags=re.I)
    value = re.sub(r"(?<![A-Za-z])RIGHT(?![A-Za-z])", "", value, flags=re.I)
    value = re.sub(r"\b(?:it)\b", "", value, flags=re.I)
    value = value.replace("!=", "≠").replace("->", "→").replace("~", " ")
    value = re.sub(r"\bover\b", "/", value, flags=re.I)
    value = value.replace("#", "; ").replace("&", " | ")
    value = re.sub(r"\^\s*\{([^{}]+)\}", r"^(\1)", value)
    value = re.sub(r"_\s*\{([^{}]+)\}", r"_(\1)", value)
    value = value.replace("{", "(").replace("}", ")")
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _fill_equation_slots(template: str, equations: list[str], slot_count: int) -> str:
    value = template
    for index in range(slot_count):
        replacement = (
            f"{_EQUATION_MARKER_START}{equations[index]}{_EQUATION_MARKER_END}"
            if index < len(equations)
            else "[수식 확인 필요]"
        )
        value = value.replace(f"\ufff0{index}\ufff1", replacement, 1)
    if len(equations) > slot_count:
        extras = "\n".join(
            f"{_EQUATION_MARKER_START}{item}{_EQUATION_MARKER_END}"
            for item in equations[slot_count:]
        )
        value = f"{value}\n{extras}" if value else extras
    return value.strip()


_ANSWER_SECTION_RE = re.compile(r"^\s*정답\s*(?:및|과)?\s*(?:해설|풀이)\s*$")


def _content_from_hwp_records(
    number: int,
    records: list[tuple[int, int, bytes]],
    *,
    stop_at_answer_section: bool = False,
) -> _HwpEndnoteContent:
    holders: list[dict[str, object]] = []
    stack: list[dict[str, object]] = []
    picture_refs: list[int] = []
    for tag, level, payload in records:
        if tag == _PARA_TEXT_TAG:
            while stack and int(stack[-1]["level"]) >= level:
                stack.pop()
            template, slot_count = _decode_hwp_paragraph_template(payload)
            holder: dict[str, object] = {
                "level": level,
                "template": template,
                "slot_count": slot_count,
                "equations": [],
            }
            holders.append(holder)
            stack.append(holder)
        elif tag == _EQEDIT_TAG:
            while stack and int(stack[-1]["level"]) >= level:
                stack.pop()
            script = _decode_hwp_equation(payload)
            if script and stack:
                equations = stack[-1]["equations"]
                assert isinstance(equations, list)
                equations.append(script)
        elif tag == _SHAPE_PICTURE_TAG and len(payload) >= 73:
            picture_id = int(struct.unpack_from("<H", payload, 71)[0])
            if picture_id and picture_id not in picture_refs:
                picture_refs.append(picture_id)

    paragraphs: list[str] = []
    equation_count = 0
    for holder in holders:
        equations = holder["equations"]
        assert isinstance(equations, list)
        value = _fill_equation_slots(
            str(holder["template"]),
            equations,
            int(holder["slot_count"]),
        )
        if stop_at_answer_section and _ANSWER_SECTION_RE.match(value):
            break
        equation_count += len(equations)
        if value:
            paragraphs.append(value)
    return _HwpEndnoteContent(
        number=number,
        paragraphs=tuple(paragraphs),
        equation_count=equation_count,
        picture_refs=tuple(picture_refs),
    )


def _collect_endnote_contents(records) -> list[_HwpEndnoteContent]:
    """Collect numbered legacy-HWP endnote text and EqEdit scripts in order."""
    notes: list[_HwpEndnoteContent] = []
    current_number: int | None = None
    current_level: int | None = None
    note_records: list[tuple[int, int, bytes]] = []

    def finish_note() -> None:
        nonlocal current_number, current_level, note_records
        if current_number is None:
            return
        notes.append(_content_from_hwp_records(current_number, note_records))
        current_number = None
        current_level = None
        note_records = []

    for tag, level, payload in records:
        if current_number is not None and current_level is not None and level <= current_level:
            finish_note()
        if tag == _CTRL_HEADER_TAG and len(payload) >= 8 and payload[:4] == _ENDNOTE_CHID:
            finish_note()
            number = struct.unpack_from("<I", payload, 4)[0]
            if 0 < number <= 999 and len(notes) < _MAX_NOTES:
                current_number = int(number)
                current_level = int(level)
            continue
        if current_number is not None:
            note_records.append((tag, level, payload))
    finish_note()
    return notes


def _collect_hwp_question_contents(records) -> list[_HwpEndnoteContent]:
    """Collect clean body content keyed by the numbered endnote anchor."""
    questions: list[_HwpEndnoteContent] = []
    current_number: int | None = None
    current_level: int | None = None
    body_started = False
    body_records: list[tuple[int, int, bytes]] = []

    def finish_question() -> None:
        nonlocal current_number, current_level, body_started, body_records
        if current_number is not None:
            content = _content_from_hwp_records(
                current_number,
                body_records,
                stop_at_answer_section=True,
            )
            if content.paragraphs or content.picture_refs:
                questions.append(content)
        current_number = None
        current_level = None
        body_started = False
        body_records = []

    for tag, level, payload in records:
        if tag == _CTRL_HEADER_TAG and len(payload) >= 8 and payload[:4] == _ENDNOTE_CHID:
            finish_question()
            number = struct.unpack_from("<I", payload, 4)[0]
            if 0 < number <= 999 and len(questions) < _MAX_NOTES:
                current_number = int(number)
                current_level = int(level)
            continue
        if current_number is None or current_level is None:
            continue
        if not body_started:
            if level > current_level:
                continue
            body_started = True
        body_records.append((tag, level, payload))
    finish_question()
    return questions


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


def _load_review_font(size: int, *, bold: bool = False):
    filename = "NotoSansKR-Bold.ttf" if bold else "NotoSansKR-Regular.ttf"
    repository_root = Path(__file__).resolve().parents[3]
    candidates = (
        repository_root / "apps" / "domains" / "assets" / "omr" / "renderer" / "fonts" / filename,
        Path("/usr/share/fonts/truetype/noto") / filename,
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("C:/Windows/Fonts/malgunbd.ttf" if bold else "C:/Windows/Fonts/malgun.ttf"),
    )
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap_review_text(
    draw: ImageDraw.ImageDraw,
    value: str,
    *,
    font,
    max_width: int,
) -> list[str]:
    lines: list[str] = []
    for source_line in str(value or "").splitlines() or [""]:
        if not source_line:
            lines.append("")
            continue
        current = ""
        for character in source_line:
            candidate = current + character
            if current and draw.textlength(candidate, font=font) > max_width:
                lines.append(current.rstrip())
                current = character.lstrip()
            else:
                current = candidate
        if current or not lines:
            lines.append(current.rstrip())
    return lines


def _find_matching_brace(value: str, start: int, *, reverse: bool = False) -> int:
    depth = 0
    indexes = range(start, -1, -1) if reverse else range(start, len(value))
    opening, closing = ("}", "{") if reverse else ("{", "}")
    for index in indexes:
        character = value[index]
        if character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _replace_hwp_fractions(value: str) -> str:
    """Replace brace-delimited HWP ``over`` forms, including nested groups."""
    search_from = 0
    while True:
        match = re.search(r"\bover\b", value[search_from:], flags=re.I)
        if not match:
            return value
        over_start = search_from + match.start()
        over_end = search_from + match.end()
        left_end = over_start - 1
        while left_end >= 0 and value[left_end].isspace():
            left_end -= 1
        right_start = over_end
        while right_start < len(value) and value[right_start].isspace():
            right_start += 1
        if left_end < 0 or right_start >= len(value):
            search_from = over_end
            continue
        if value[left_end] != "}" or value[right_start] != "{":
            search_from = over_end
            continue
        left_start = _find_matching_brace(value, left_end, reverse=True)
        right_end = _find_matching_brace(value, right_start)
        if left_start < 0 or right_end < 0:
            search_from = over_end
            continue
        numerator = _replace_hwp_fractions(value[left_start + 1 : left_end])
        denominator = _replace_hwp_fractions(value[right_start + 1 : right_end])
        replacement = rf"\frac{{{numerator}}}{{{denominator}}}"
        value = value[:left_start] + replacement + value[right_end + 1 :]
        search_from = max(left_start - 1, 0)


def _split_hwp_top_level(value: str, delimiter: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for index, character in enumerate(value):
        if character == "{":
            depth += 1
        elif character == "}":
            depth = max(depth - 1, 0)
        elif character == delimiter and depth == 0:
            parts.append(value[start:index])
            start = index + 1
    parts.append(value[start:])
    return parts


def _replace_hwp_cases(value: str) -> str:
    search_from = 0
    while True:
        match = re.search(r"\bcases\s*\{", value[search_from:], flags=re.I)
        if not match:
            return value
        start = search_from + match.start()
        brace_start = search_from + match.end() - 1
        brace_end = _find_matching_brace(value, brace_start)
        if brace_end < 0:
            return value
        rows = []
        for raw_row in _split_hwp_top_level(
            value[brace_start + 1 : brace_end],
            "#",
        ):
            columns = [
                column
                for column in _split_hwp_top_level(raw_row, "&")
                if column.strip()
            ]
            rows.append(r",\;".join(columns))
        replacement = r"\left\{ " + r";\quad ".join(rows) + r" \right."
        value = value[:start] + replacement + value[brace_end + 1 :]
        search_from = start + len(replacement)


def _hwp_equation_to_mathtext(script: str) -> str:
    value = str(script or "").replace("`", " ")
    value = re.sub(
        r"(?<=[A-Za-z0-9)\]])\s*ge(?!q)\s*(?=[A-Za-z0-9(])",
        lambda _match: r"\geq ",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"(?<=[A-Za-z0-9)\]])\s*ne(?!q)\s*(?=[A-Za-z0-9(])",
        lambda _match: r"\neq ",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"((?<![A-Za-z])[A-Za-z0-9]|[)\]])\s*le(?!q)\s*"
        r"(?=(?:alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|phi|omega|"
        r"[A-Za-z](?![A-Za-z])))",
        lambda match: f"{match.group(1)}\\leq ",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"(?<=[A-Za-z0-9)\]])\s*leq\s*(?=[A-Za-z0-9(-])",
        lambda _match: r"\leq ",
        value,
        flags=re.I,
    )
    value = re.sub(r"(?<![A-Za-z])it(?=[A-Za-z-])", "", value, flags=re.I)
    value = re.sub(
        r"(?<=[0-9])pi(?![A-Za-z])",
        lambda _match: r"\pi",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"(?<![A-Za-z])(sin|cos|tan|log|ln)(?=[A-Za-z])",
        r"\1 ",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"(?<![A-Za-z0-9])([A-Za-z0-9]+)\s+over\s+([A-Za-z0-9]+)(?![A-Za-z0-9])",
        lambda match: rf"\frac{{{match.group(1)}}}{{{match.group(2)}}}",
        value,
        flags=re.I,
    )
    value = _replace_hwp_fractions(value)
    value = re.sub(
        r"\broot\s*\{([^{}]+)\}\s*of\s*\{([^{}]+)\}",
        r"\\sqrt[\1]{\2}",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"\brm\s*\{([A-Za-z0-9]+)(?:\}|$)",
        r"\\mathrm{\1}",
        value,
    )
    value = re.sub(r"\brm\s*\{([^{}]+)\}", r"\\mathrm{\1}", value)
    value = re.sub(r"\brm\s*([A-Za-z]+)", r"\\mathrm{\1}", value)
    value = re.sub(r"\brm\s*([0-9]+)", r"\1", value)
    value = value.replace("또는", r"\mathrm{or}")
    value = re.sub(
        r"(?<![A-Za-z])sqrt\s*([0-9]+)",
        lambda match: rf"\sqrt{{{match.group(1)}}}",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"(?<![A-Za-z])root\s*([0-9]+)",
        lambda match: rf"\sqrt{{{match.group(1)}}}",
        value,
        flags=re.I,
    )
    value = re.sub(r"(?<![A-Za-z])pile\s*\{[^{}]*\}", "", value, flags=re.I)

    value = _replace_hwp_cases(value)
    replacements = {
        "THEREFORE": r"\therefore",
        "BECAUSE": r"\because",
        "rarrow": r"\rightarrow",
        "rightarrow": r"\rightarrow",
        "larrow": r"\leftarrow",
        "INF": r"\infty",
        "GEQ": r"\geq",
        "LEQ": r"\leq",
        "NEQ": r"\neq",
        "TIMES": r"\times",
        "CDOT": r"\cdot",
        "DIVIDE": r"\div",
        "PLUSMINUS": r"\pm",
        "ANGLE": r"\angle",
        "DEG": r"^{\circ}",
        "NEARROW": r"\nearrow",
        "SEARROW": r"\searrow",
        "CUP": r"\cup",
        "CAP": r"\cap",
        "alpha": r"\alpha",
        "beta": r"\beta",
        "gamma": r"\gamma",
        "delta": r"\delta",
        "theta": r"\theta",
        "lambda": r"\lambda",
        "mu": r"\mu",
        "pi": r"\pi",
        "sigma": r"\sigma",
        "phi": r"\phi",
        "omega": r"\omega",
    }
    for token, replacement in replacements.items():
        value = re.sub(
            rf"(?<![A-Za-z\\]){re.escape(token)}(?![A-Za-z])",
            lambda _match: replacement,
            value,
            flags=re.I,
        )
    for operator in (
        "lim",
        "sum",
        "int",
        "sqrt",
        "sin",
        "cos",
        "tan",
        "log",
        "ln",
        "cdots",
        "ldots",
    ):
        value = re.sub(
            rf"(?<!\\)\b{operator}\b",
            lambda _match, name=operator: f"\\{name}",
            value,
            flags=re.I,
        )
    value = re.sub(
        r"(?<![A-Za-z\\])LEFT(?![A-Za-z])",
        lambda _match: r"\left",
        value,
        flags=re.I,
    )
    value = re.sub(
        r"(?<![A-Za-z\\])RIGHT(?![A-Za-z])",
        lambda _match: r"\right",
        value,
        flags=re.I,
    )
    value = re.sub(r"\\left\s*\{", lambda _match: r"\left\{", value)
    value = re.sub(r"\\right\s*\}", lambda _match: r"\right\}", value)
    value = re.sub(r"\\left\s*\|", lambda _match: r"\left|", value)
    value = re.sub(r"\\right\s*\|", lambda _match: r"\right|", value)
    value = re.sub(r"\\right\.\s*\\right\.", lambda _match: r"\right.", value)
    value = re.sub(
        r"(?<![A-Za-z])([A-Za-z])\s*prime(?![A-Za-z])",
        lambda match: rf"{match.group(1)}^{{\prime}}",
        value,
        flags=re.I,
    )
    value = re.sub(r"\b(?:it)\b", "", value, flags=re.I)
    value = value.replace("!=", r"\neq").replace("->", r"\rightarrow")
    value = value.replace("~", r"\;")
    value = value.replace("#", r"\quad;\quad").replace("&", r"\quad|\quad")
    value = re.sub(r"\bmatrix\s*\{", r"\\left( ", value, flags=re.I)
    value = re.sub(r"(?<!\\)\bbar\b", lambda _match: r"\overline", value, flags=re.I)
    value = re.sub(r"\\sqrt\s*\(([^()]*)\)", r"\\sqrt{\1}", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _render_equation_image(script: str, *, font_size: int = 26) -> Image.Image:
    output = BytesIO()
    try:
        from matplotlib.font_manager import FontProperties
        from matplotlib.mathtext import math_to_image

        latex = _hwp_equation_to_mathtext(script)
        math_to_image(
            f"${latex}$",
            output,
            prop=FontProperties(size=font_size),
            dpi=120,
            format="png",
            color="#111827",
        )
        output.seek(0)
        with Image.open(output) as source:
            rgba = source.convert("RGBA")
            canvas = Image.new("RGB", rgba.size, "white")
            canvas.paste(rgba, mask=rgba.getchannel("A"))
            rgba.close()
            return canvas
    except Exception:
        logger.info(
            "HWP_EQUATION_MATHTEXT_FALLBACK | script=%r",
            str(script or "")[:160],
        )
        fallback = _humanize_hwp_equation(script)
        font = _load_review_font(font_size)
        scratch = Image.new("RGB", (10, 10), "white")
        draw = ImageDraw.Draw(scratch)
        box = draw.textbbox((0, 0), fallback, font=font)
        scratch.close()
        canvas = Image.new("RGB", (max(box[2] + 8, 16), max(box[3] - box[1] + 8, 16)), "white")
        ImageDraw.Draw(canvas).text((4, 4 - box[1]), fallback, font=font, fill="#111827")
        return canvas


def _render_text_fragment(value: str, *, font) -> Image.Image | None:
    text = str(value or "").strip()
    if not text:
        return None
    scratch = Image.new("RGB", (10, 10), "white")
    draw = ImageDraw.Draw(scratch)
    box = draw.textbbox((0, 0), text, font=font)
    scratch.close()
    image = Image.new(
        "RGB",
        (max(box[2] - box[0] + 8, 16), max(box[3] - box[1] + 8, 16)),
        "white",
    )
    ImageDraw.Draw(image).text((4 - box[0], 4 - box[1]), text, font=font, fill="#111827")
    return image


def _render_paragraph_flow(paragraph: str, *, font, max_width: int) -> Image.Image:
    scratch = Image.new("RGB", (max_width, 100), "white")
    measure = ImageDraw.Draw(scratch)
    atoms: list[tuple[Image.Image, bool]] = []
    parts = re.split(
        f"({_EQUATION_MARKER_START}.*?{_EQUATION_MARKER_END})",
        paragraph,
        flags=re.S,
    )
    for part in parts:
        if not part:
            continue
        if part.startswith(_EQUATION_MARKER_START) and part.endswith(_EQUATION_MARKER_END):
            equation = _render_equation_image(part[1:-1])
            if equation.width > max_width:
                scale = max_width / equation.width
                resized = equation.resize(
                    (max_width, max(1, round(equation.height * scale))),
                    Image.Resampling.LANCZOS,
                )
                equation.close()
                equation = resized
            atoms.append((equation, False))
            continue
        logical_lines = part.splitlines() or [part]
        for line_index, logical_line in enumerate(logical_lines):
            wrapped = _wrap_review_text(
                measure,
                logical_line,
                font=font,
                max_width=max_width,
            )
            for wrap_index, line in enumerate(wrapped):
                fragment = _render_text_fragment(line, font=font)
                if fragment is None:
                    continue
                forced_break = wrap_index < len(wrapped) - 1 or line_index < len(logical_lines) - 1
                atoms.append((fragment, forced_break))
    scratch.close()

    rows: list[list[Image.Image]] = []
    current: list[Image.Image] = []
    current_width = 0
    gap = 10
    for atom, forced_break in atoms:
        required = atom.width + (gap if current else 0)
        if current and current_width + required > max_width:
            rows.append(current)
            current = []
            current_width = 0
            required = atom.width
        current.append(atom)
        current_width += required
        if forced_break:
            rows.append(current)
            current = []
            current_width = 0
    if current:
        rows.append(current)
    if not rows:
        return Image.new("RGB", (max_width, 1), "white")

    row_gap = 12
    row_heights = [max(image.height for image in row) for row in rows]
    canvas = Image.new(
        "RGB",
        (max_width, sum(row_heights) + row_gap * max(len(rows) - 1, 0)),
        "white",
    )
    y = 0
    for row, row_height in zip(rows, row_heights):
        x = 0
        for image in row:
            canvas.paste(image, (x, y + row_height - image.height))
            x += image.width + gap
            image.close()
        y += row_height + row_gap
    return canvas


def _render_source_content(
    *,
    content: _HwpEndnoteContent,
    images: list[Image.Image],
    label: str,
) -> tuple[bytes, int, int]:
    """Render source text, native equations, and source images for review."""
    width = 1600
    margin = 72
    body_font = _load_review_font(34)
    label_font = _load_review_font(24, bold=True)
    scratch = Image.new("RGB", (width, 200), "white")
    draw = ImageDraw.Draw(scratch)
    label_line_height = max(36, int(draw.textbbox((0, 0), "가Ag", font=label_font)[3] * 1.35))
    max_text_width = width - (margin * 2)
    paragraph_images = [
        _render_paragraph_flow(
            paragraph,
            font=body_font,
            max_width=max_text_width,
        )
        for paragraph in content.paragraphs
        if paragraph.strip()
    ]

    scaled_images: list[tuple[Image.Image, int, int]] = []
    for image in images:
        scale = min(max_text_width / max(image.width, 1), 1.0)
        target_width = max(1, round(image.width * scale))
        target_height = max(1, round(image.height * scale))
        scaled_images.append((image, target_width, target_height))

    height = margin + label_line_height + 28
    height += sum(image.height + 22 for image in paragraph_images)
    if scaled_images:
        height += 18
        height += sum(item[2] + 28 for item in scaled_images)
    height += margin
    if width * height > _MAX_IMAGE_PIXELS:
        raise ValueError("HWP 원문 재현 이미지가 안전 처리 한도를 초과했습니다.")

    canvas = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    y = margin
    draw.rounded_rectangle(
        (margin, y, width - margin, y + label_line_height + 12),
        radius=12,
        fill="#f3f5f8",
    )
    draw.text(
        (margin + 20, y + 4),
        label,
        font=label_font,
        fill="#394150",
    )
    y += label_line_height + 28
    for paragraph_image in paragraph_images:
        canvas.paste(paragraph_image, (margin, y))
        y += paragraph_image.height + 22
        paragraph_image.close()
    if scaled_images:
        draw.line((margin, y, width - margin, y), fill="#d7dce3", width=2)
        y += 18
        for image, target_width, target_height in scaled_images:
            rendered = image
            if (target_width, target_height) != image.size:
                rendered = image.resize((target_width, target_height), Image.Resampling.LANCZOS)
            canvas.paste(rendered, ((width - target_width) // 2, y))
            if rendered is not image:
                rendered.close()
            y += target_height + 28

    output = BytesIO()
    canvas.save(output, format="PNG", compress_level=2)
    data = output.getvalue()
    canvas.close()
    scratch.close()
    return data, width, height


def _render_endnote_content(
    *,
    content: _HwpEndnoteContent,
    images: list[Image.Image],
) -> tuple[bytes, int, int]:
    return _render_source_content(
        content=content,
        images=images,
        label=f"미주 {content.number}번 · 선생님 원문(문자·수식·삽화 재현)",
    )


def _render_problem_content(
    *,
    content: _HwpEndnoteContent,
    images: list[Image.Image],
) -> tuple[bytes, int, int]:
    return _render_source_content(
        content=content,
        images=images,
        label=f"{content.number}번 · 한글 본문 원문(문자·수식·삽화 재현)",
    )


def extract_hwp_endnotes(
    path: str,
    *,
    include_paired_reconstruction: bool = False,
    include_problem_reconstruction: bool = False,
) -> HwpEndnoteExtraction:
    """Return legacy HWP endnote coverage and source visuals."""
    import olefile

    if not olefile.isOleFile(path):
        raise ValueError("지원되는 HWP 5.x 문서가 아닙니다.")

    with olefile.OleFileIO(path) as ole:
        notes: list[tuple[int, list[int]]] = []
        contents: list[_HwpEndnoteContent] = []
        problem_contents: list[_HwpEndnoteContent] = []
        control_numbers: list[int] = []
        for section in _read_body_sections(ole):
            records = list(_iter_records(section))
            notes.extend(_collect_endnote_picture_ids(iter(records)))
            if include_paired_reconstruction:
                contents.extend(_collect_endnote_contents(iter(records)))
            if include_problem_reconstruction:
                problem_contents.extend(_collect_hwp_question_contents(iter(records)))
            for number in _collect_endnote_numbers(iter(records)):
                if number not in control_numbers:
                    control_numbers.append(number)
        streams = _bindata_streams_by_id(ole)
        visuals: list[HwpEndnoteVisual] = []
        paired_visuals: list[HwpEndnoteVisual] = []
        problem_visuals: list[HwpEndnoteVisual] = []
        seen_numbers: set[int] = set()
        picture_ids_by_number = {number: picture_ids for number, picture_ids in notes}
        content_by_number = {content.number: content for content in contents}
        for number in control_numbers:
            if number in seen_numbers:
                logger.warning("HWP_ENDNOTE_DUPLICATE_NUMBER | number=%s", number)
                continue
            picture_ids = picture_ids_by_number.get(number, [])
            images = []
            for picture_id in picture_ids:
                stream_name = streams.get(picture_id)
                if not stream_name:
                    continue
                image = _load_picture(ole.openstream(stream_name).read())
                if image is not None:
                    images.append(image)
            try:
                if images:
                    png_bytes, width, height = _stack_as_png(images)
                    visuals.append(
                        HwpEndnoteVisual(
                            number=number,
                            png_bytes=png_bytes,
                            width=width,
                            height=height,
                            picture_count=len(images),
                        )
                    )
                content = content_by_number.get(number)
                if include_paired_reconstruction and content and (content.paragraphs or images):
                    png_bytes, width, height = _render_endnote_content(
                        content=content,
                        images=images,
                    )
                    paired_visuals.append(
                        HwpEndnoteVisual(
                            number=number,
                            png_bytes=png_bytes,
                            width=width,
                            height=height,
                            picture_count=len(images),
                            render_mode="source_content_reconstruction",
                        )
                    )
                elif include_paired_reconstruction and images:
                    paired_visuals.append(visuals[-1])
            finally:
                for image in images:
                    image.close()
            seen_numbers.add(number)
        if include_problem_reconstruction:
            problem_content_by_number = {
                content.number: content for content in problem_contents
            }
            for number in control_numbers:
                content = problem_content_by_number.get(number)
                if content is None:
                    continue
                images = []
                for picture_ref in content.picture_refs:
                    if not isinstance(picture_ref, int):
                        continue
                    stream_name = streams.get(picture_ref)
                    if not stream_name:
                        continue
                    image = _load_picture(ole.openstream(stream_name).read())
                    if image is not None:
                        images.append(image)
                try:
                    png_bytes, width, height = _render_problem_content(
                        content=content,
                        images=images,
                    )
                    problem_visuals.append(
                        HwpEndnoteVisual(
                            number=number,
                            png_bytes=png_bytes,
                            width=width,
                            height=height,
                            picture_count=len(images),
                            render_mode="source_body_reconstruction",
                        )
                    )
                finally:
                    for image in images:
                        image.close()
    return HwpEndnoteExtraction(
        control_numbers=tuple(control_numbers),
        visuals=tuple(sorted(visuals, key=lambda item: item.number)),
        paired_visuals=tuple(sorted(paired_visuals, key=lambda item: item.number)),
        problem_visuals=tuple(sorted(problem_visuals, key=lambda item: item.number)),
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


def _hwpx_body_content(element: ElementTree.Element) -> tuple[tuple[str, ...], tuple[str, ...]]:
    parts: list[str] = []
    image_refs: list[str] = []

    def walk(node: ElementTree.Element) -> None:
        name = node.tag.rsplit("}", 1)[-1]
        if name == "endNote":
            return
        if name == "t":
            parts.append(str(node.text or ""))
            for child in node:
                walk(child)
                parts.append(str(child.tail or ""))
            return
        if name == "equation":
            script = next(
                (
                    str(item.text or "").strip()
                    for item in node.iter()
                    if item.tag.rsplit("}", 1)[-1] == "script" and item.text
                ),
                "",
            )
            if script:
                parts.append(f"{_EQUATION_MARKER_START}{script}{_EQUATION_MARKER_END}")
            return
        if name == "img":
            image_ref = str(node.attrib.get("binaryItemIDRef") or "").strip().lower()
            if image_ref and image_ref not in image_refs:
                image_refs.append(image_ref)
            return
        if name == "tab":
            parts.append("\u2003\u2003")
            return
        if name == "lineBreak":
            parts.append("\n")
            return
        for child in node:
            walk(child)
            parts.append(str(child.tail or ""))
        if name in {"p", "tr"}:
            parts.append("\n")

    walk(element)
    value = "".join(parts)
    value = re.sub(r"[ \u00a0]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    paragraphs = tuple(line.strip() for line in value.splitlines() if line.strip())
    return paragraphs, tuple(image_refs)


def _collect_hwpx_question_contents(
    roots: list[ElementTree.Element],
) -> list[_HwpEndnoteContent]:
    questions: list[_HwpEndnoteContent] = []
    current_number: int | None = None
    current_paragraphs: list[str] = []
    current_image_refs: list[str] = []

    def finish_question() -> None:
        nonlocal current_number, current_paragraphs, current_image_refs
        if current_number is not None and (current_paragraphs or current_image_refs):
            questions.append(
                _HwpEndnoteContent(
                    number=current_number,
                    paragraphs=tuple(current_paragraphs),
                    equation_count=sum(
                        paragraph.count(_EQUATION_MARKER_START)
                        for paragraph in current_paragraphs
                    ),
                    picture_refs=tuple(current_image_refs),
                )
            )
        current_number = None
        current_paragraphs = []
        current_image_refs = []

    for root in roots:
        paragraphs = [
            child for child in root if child.tag.rsplit("}", 1)[-1] == "p"
        ]
        for paragraph in paragraphs:
            endnotes = [
                item
                for item in paragraph.iter()
                if item.tag.rsplit("}", 1)[-1] == "endNote"
            ]
            body, image_refs = _hwpx_body_content(paragraph)
            if endnotes:
                finish_question()
                raw_number = str(endnotes[0].attrib.get("number") or "")
                if raw_number.isdigit() and 0 < int(raw_number) <= 999:
                    current_number = int(raw_number)
            if current_number is None:
                continue
            if any(_ANSWER_SECTION_RE.match(value) for value in body):
                finish_question()
                return questions
            current_paragraphs.extend(body)
            for image_ref in image_refs:
                if image_ref not in current_image_refs:
                    current_image_refs.append(image_ref)
    finish_question()
    return questions


def extract_hwpx_endnotes(
    path: str,
    *,
    include_problem_reconstruction: bool = False,
) -> HwpEndnoteExtraction:
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
        roots: list[ElementTree.Element] = []

        for section_name in section_names:
            try:
                root = ElementTree.fromstring(archive.read(section_name))
            except ElementTree.ParseError:
                continue
            roots.append(root)
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

        problem_visuals: list[HwpEndnoteVisual] = []
        if include_problem_reconstruction:
            for content in _collect_hwpx_question_contents(roots):
                images = []
                for picture_ref in content.picture_refs:
                    if not isinstance(picture_ref, str):
                        continue
                    member_name = image_members.get(picture_ref)
                    if not member_name:
                        continue
                    image = _load_picture(archive.read(member_name))
                    if image is not None:
                        images.append(image)
                try:
                    png_bytes, width, height = _render_problem_content(
                        content=content,
                        images=images,
                    )
                    problem_visuals.append(
                        HwpEndnoteVisual(
                            number=content.number,
                            png_bytes=png_bytes,
                            width=width,
                            height=height,
                            picture_count=len(images),
                            render_mode="source_body_reconstruction",
                        )
                    )
                finally:
                    for image in images:
                        image.close()

    return HwpEndnoteExtraction(
        control_numbers=tuple(control_numbers),
        visuals=tuple(sorted(visuals, key=lambda item: item.number)),
        paired_visuals=tuple(sorted(visuals, key=lambda item: item.number)),
        problem_visuals=tuple(sorted(problem_visuals, key=lambda item: item.number)),
    )


def extract_document_endnotes(
    path: str,
    filename: str,
    *,
    include_paired_reconstruction: bool = False,
    include_problem_reconstruction: bool = False,
) -> HwpEndnoteExtraction:
    suffix = PurePosixPath(str(filename or "").lower()).suffix
    if suffix == ".hwp":
        return extract_hwp_endnotes(
            path,
            include_paired_reconstruction=include_paired_reconstruction,
            include_problem_reconstruction=include_problem_reconstruction,
        )
    if suffix == ".hwpx":
        return extract_hwpx_endnotes(
            path,
            include_problem_reconstruction=include_problem_reconstruction,
        )
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
