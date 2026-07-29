import io
import re
import struct
import unicodedata
import zipfile
import zlib
from collections import Counter, defaultdict
from pathlib import Path
from xml.etree import ElementTree

from apps.domains.tools.problem_studio.structure import normalize_space


DEFAULT_MAX_ZIP_MEMBERS = 400
DEFAULT_MAX_ZIP_UNCOMPRESSED_BYTES = 180 * 1024 * 1024
_HWPX_ANSWER_KEY_MARKER_RE = re.compile(
    r"(?:유형출제\s*)?정답\s*(?:및|/)\s*(?:해설|풀이)?",
)
_HWP_ENDNOTE_ANCHOR = "[[HWP-ENDNOTE-ANCHOR]]"
_HWP_QUESTION_BODY_START_RE = re.compile(
    r"^(?:"
    r"[①②③④⑤⑥⑦⑧⑨]"
    r"|<\s*보\s*기\s*>"
    r"|.*(?:고른|옳은|옳지 않은|적절한|해당하는|설명으로).*(?:것은|것인가)\??"
    r"|.*(?:고르시오|서술하시오|설명하시오)\.?"
    r")$"
)


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
    paragraphs: list[str] = []
    for paragraph in root.iter():
        if paragraph.tag.rsplit("}", 1)[-1] != "p":
            continue
        parts: list[str] = []
        for elem in paragraph.iter():
            tag = elem.tag.rsplit("}", 1)[-1]
            if tag == "t" and elem.text:
                parts.append(elem.text)
            elif tag == "tab":
                parts.append("\t")
            elif tag == "lineBreak":
                parts.append("\n")
        value = "".join(parts).strip()
        if value:
            paragraphs.append(value)
    if paragraphs:
        return "\n".join(paragraphs)

    texts: list[str] = []
    for elem in root.iter():
        if elem.text and elem.text.strip():
            tag = elem.tag.rsplit("}", 1)[-1]
            if tag in {"t", "p"}:
                texts.append(elem.text.strip())
    return " ".join(texts)


def _hwpx_text_node_value(element: ElementTree.Element) -> str:
    parts = [element.text or ""]
    for child in element:
        name = child.tag.rsplit("}", 1)[-1]
        if name == "tab":
            parts.append("\t")
        elif name == "lineBreak":
            parts.append("\n")
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


def hwpx_xml_text(xml_bytes: bytes) -> str:
    """Extract each HWPX paragraph once, including native equation scripts."""
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return ""

    paragraphs: list[str] = []
    for paragraph in root.iter():
        if paragraph.tag.rsplit("}", 1)[-1] != "p":
            continue
        parts: list[str] = []
        for run in paragraph:
            if run.tag.rsplit("}", 1)[-1] != "run":
                continue
            for child in run:
                tag = child.tag.rsplit("}", 1)[-1]
                if tag == "t":
                    parts.append(_hwpx_text_node_value(child))
                elif tag == "tab":
                    parts.append("\t")
                elif tag == "lineBreak":
                    parts.append("\n")
                elif tag == "equation":
                    script = next(
                        (
                            item.text
                            for item in child.iter()
                            if item.tag.rsplit("}", 1)[-1] == "script" and item.text
                        ),
                        "",
                    )
                    if script:
                        parts.extend((" [[수식:", script, "]] "))
        value = "".join(parts).strip()
        if value:
            paragraphs.append(value)
    return "\n".join(paragraphs)


def _hwpx_element_text(element: ElementTree.Element, *, skip_end_notes: bool) -> str:
    parts: list[str] = []

    def walk(node: ElementTree.Element) -> None:
        name = node.tag.rsplit("}", 1)[-1]
        if skip_end_notes and name == "endNote":
            return
        if name == "t":
            parts.append(_hwpx_text_node_value(node))
            return
        if name == "equation":
            script = next(
                (
                    item.text
                    for item in node.iter()
                    if item.tag.rsplit("}", 1)[-1] == "script" and item.text
                ),
                "",
            )
            if script:
                parts.extend((" [[수식:", script, "]] "))
            return
        if name == "tbl":
            rows = [
                item for item in node if item.tag.rsplit("}", 1)[-1] == "tr"
            ]
            for row in rows:
                cells: list[str] = []
                for cell in row:
                    if cell.tag.rsplit("}", 1)[-1] != "tc":
                        continue
                    cell_text = normalize_space(
                        _hwpx_element_text(cell, skip_end_notes=skip_end_notes)
                    ).replace("\n", " ")
                    cells.append(cell_text)
                if any(cells):
                    parts.append("\t".join(cells).strip())
                    parts.append("\n")
            return
        if name == "tab":
            parts.append("\t")
            return
        if name == "lineBreak":
            parts.append("\n")
            return
        for child in node:
            walk(child)
        if name in {"p", "tr"}:
            parts.append("\n")

    walk(element)
    return normalize_space("".join(parts))


def _hwpx_end_note_text(end_note: ElementTree.Element) -> str:
    raw = _hwpx_element_text(end_note, skip_end_notes=False)
    lines = [line for line in raw.splitlines() if line.strip()]
    answer_index = next(
        (index for index, line in enumerate(lines) if re.match(r"^정답\s*[:：]?", line)),
        None,
    )
    if answer_index is None:
        return f"해설:\n{raw}" if raw else ""
    answer_line = lines[answer_index]
    explanation_lines = lines[:answer_index] + lines[answer_index + 1:]
    explanation = normalize_space("\n".join(explanation_lines))
    return (
        f"{answer_line}\n해설:\n{explanation}"
        if explanation
        else answer_line
    )


def hwpx_section_text(xml_bytes: bytes) -> str:
    """Rebuild top-level HWPX questions using their numbered endnote controls."""
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return ""
    paragraphs = [
        child for child in root if child.tag.rsplit("}", 1)[-1] == "p"
    ]
    if not any(
        item.tag.rsplit("}", 1)[-1] == "endNote"
        for paragraph in paragraphs
        for item in paragraph.iter()
    ):
        return hwpx_xml_text(xml_bytes)

    prefix: list[str] = []
    blocks: list[str] = []
    current_lines: list[str] = []
    current_explanation = ""

    def flush() -> None:
        nonlocal current_lines, current_explanation
        if not current_lines:
            return
        if current_explanation:
            current_lines.append(current_explanation)
        block = normalize_space("\n".join(current_lines))
        if block:
            blocks.append(block)
        current_lines = []
        current_explanation = ""

    for paragraph in paragraphs:
        end_notes = [
            item
            for item in paragraph.iter()
            if item.tag.rsplit("}", 1)[-1] == "endNote"
        ]
        body = _hwpx_element_text(paragraph, skip_end_notes=True)
        if end_notes:
            flush()
            number = end_notes[0].attrib.get("number") or str(len(blocks) + 1)
            current_lines = [f"{number}. {body}".strip()]
            current_explanation = _hwpx_end_note_text(end_notes[0])
        elif body:
            if current_lines and _HWPX_ANSWER_KEY_MARKER_RE.search(body):
                flush()
                break
            if current_lines:
                current_lines.append(body)
            else:
                prefix.append(body)
    flush()
    return normalize_space("\n\n".join([*prefix, *blocks]))


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
        section_names = sorted(
            (
                name
                for name in names
                if re.fullmatch(r"Contents/section\d+\.xml", name, re.IGNORECASE)
            ),
            key=lambda name: int(re.search(r"\d+", Path(name).stem).group()),
        )
        chunks = [hwpx_section_text(zf.read(name)) for name in section_names]
        section_text = normalize_space("\n".join(chunks))
        if section_text:
            if "[[수식:" in section_text:
                return section_text
            if "Preview/PrvText.txt" in names:
                preview_text = normalize_space(
                    zf.read("Preview/PrvText.txt").decode("utf-8", "ignore")
                )
                preview_line_count = len(preview_text.splitlines())
                section_line_count = max(1, len(section_text.splitlines()))
                if (
                    preview_text
                    and len(preview_text) >= len(section_text) * 0.55
                    and preview_line_count >= section_line_count * 0.8
                ):
                    return preview_text
            return section_text
        if "Preview/PrvText.txt" in names:
            return normalize_space(zf.read("Preview/PrvText.txt").decode("utf-8", "ignore"))
    return ""


def extract_hwpx_document_profile(data: bytes) -> dict[str, object]:
    """Return the dominant editable typography and page layout from an HWPX."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = safe_zip_members(zf)
        names = [member.filename for member in members]
        if "Contents/header.xml" not in names:
            return {}
        try:
            header = ElementTree.fromstring(zf.read("Contents/header.xml"))
        except ElementTree.ParseError:
            return {}
        section_names = sorted(
            (
                name
                for name in names
                if re.fullmatch(r"Contents/section\d+\.xml", name, re.IGNORECASE)
            ),
            key=lambda name: int(re.search(r"\d+", Path(name).stem).group()),
        )
        sections = []
        for name in section_names:
            try:
                sections.append(ElementTree.fromstring(zf.read(name)))
            except ElementTree.ParseError:
                continue

    def descendants(element: ElementTree.Element, name: str):
        return (
            item
            for item in element.iter()
            if item.tag.rsplit("}", 1)[-1] == name
        )

    hangul_fonts: dict[str, str] = {}
    registered_fonts: set[str] = set()
    for fontface in descendants(header, "fontface"):
        language = fontface.attrib.get("lang")
        for font in descendants(fontface, "font"):
            font_id = font.attrib.get("id")
            family = font.attrib.get("face")
            if not family:
                continue
            registered_fonts.add(family)
            if language == "HANGUL" and font_id is not None:
                hangul_fonts[font_id] = family

    char_properties: dict[str, tuple[str, float, int, int]] = {}
    for char_pr in descendants(header, "charPr"):
        char_id = char_pr.attrib.get("id")
        if char_id is None:
            continue
        font_ref = next(descendants(char_pr, "fontRef"), None)
        ratio = next(descendants(char_pr, "ratio"), None)
        spacing = next(descendants(char_pr, "spacing"), None)
        font_id = font_ref.attrib.get("hangul") if font_ref is not None else ""
        char_properties[char_id] = (
            hangul_fonts.get(font_id or "", ""),
            int(char_pr.attrib.get("height") or 1000) / 100,
            int(ratio.attrib.get("hangul") or 100) if ratio is not None else 100,
            int(spacing.attrib.get("hangul") or 0) if spacing is not None else 0,
        )

    paragraph_properties: dict[str, tuple[str, int]] = {}
    for para_pr in descendants(header, "paraPr"):
        para_id = para_pr.attrib.get("id")
        if para_id is None:
            continue
        line_spacing = next(descendants(para_pr, "lineSpacing"), None)
        if line_spacing is not None:
            paragraph_properties[para_id] = (
                str(line_spacing.attrib.get("type") or ""),
                int(line_spacing.attrib.get("value") or 0),
            )

    char_weights: Counter[tuple[str, float, int, int]] = Counter()
    paragraph_weights: Counter[tuple[str, int]] = Counter()
    page_pr: ElementTree.Element | None = None
    column_pr: ElementTree.Element | None = None
    page_breaks = 0
    for section in sections:
        for paragraph in descendants(section, "p"):
            para_id = paragraph.attrib.get("paraPrIDRef") or ""
            paragraph_text = "".join(
                _hwpx_text_node_value(child)
                for run in paragraph
                if run.tag.rsplit("}", 1)[-1] == "run"
                for child in run
                if child.tag.rsplit("}", 1)[-1] == "t"
            ).strip()
            if paragraph_text and para_id in paragraph_properties:
                paragraph_weights[paragraph_properties[para_id]] += len(paragraph_text)
            page_breaks += int(paragraph.attrib.get("pageBreak") or 0)
        for run in descendants(section, "run"):
            char_id = run.attrib.get("charPrIDRef") or ""
            run_text = "".join(
                _hwpx_text_node_value(child)
                for child in run
                if child.tag.rsplit("}", 1)[-1] == "t"
            ).strip()
            if run_text and char_id in char_properties:
                char_weights[char_properties[char_id]] += len(run_text)
        if page_pr is None:
            page_pr = next(descendants(section, "pagePr"), None)
        column_candidates = list(descendants(section, "colPr"))
        two_column = next(
            (
                item
                for item in column_candidates
                if int(item.attrib.get("colCount") or 1) == 2
            ),
            None,
        )
        if column_pr is None:
            column_pr = two_column or (column_candidates[0] if column_candidates else None)

    dominant_char = char_weights.most_common(1)
    dominant_paragraph = paragraph_weights.most_common(1)
    profile: dict[str, object] = {
        "schema": "problem-studio-hwpx-profile/v1",
        "registered_fonts": sorted(registered_fonts),
        "estimated_min_pages": page_breaks + 1,
    }
    if dominant_char:
        font_family, size_pt, width_ratio, letter_spacing = dominant_char[0][0]
        profile.update({
            "body_font_family": font_family,
            "body_size_pt": size_pt,
            "body_width_ratio_percent": width_ratio,
            "body_letter_spacing_percent": letter_spacing,
        })
    if dominant_paragraph:
        line_spacing_type, line_spacing_value = dominant_paragraph[0][0]
        if line_spacing_type == "PERCENT":
            profile["line_spacing_percent"] = line_spacing_value
    if page_pr is not None:
        margin = next(descendants(page_pr, "margin"), None)
        profile.update({
            "page_width_pt": int(page_pr.attrib.get("width") or 0) / 100,
            "page_height_pt": int(page_pr.attrib.get("height") or 0) / 100,
        })
        if margin is not None:
            profile["margins_mm"] = {
                key: round(
                    int(margin.attrib.get(key) or 0) * 25.4 / 7200,
                    2,
                )
                for key in ("top", "bottom", "left", "right")
            }
    if column_pr is not None:
        line = next(descendants(column_pr, "colLine"), None)
        profile.update({
            "column_count": int(column_pr.attrib.get("colCount") or 1),
            "column_gap_mm": round(
                int(column_pr.attrib.get("sameGap") or 0) * 25.4 / 7200,
                2,
            ),
            "center_line": bool(
                line is not None and line.attrib.get("type") not in {None, "NONE"}
            ),
            "center_line_style": (
                str(line.attrib.get("type") or "SOLID") if line is not None else "SOLID"
            ),
        })
    return profile


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


def extract_hwpx_question_images(data: bytes) -> list[dict[str, object]]:
    """Extract the largest native picture associated with each numbered question."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        members = safe_zip_members(zf)
        names = {member.filename for member in members}
        image_members = {
            Path(name).stem: name
            for name in names
            if name.startswith("BinData/")
            and Path(name).suffix.lower()
            in {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
        }
        if not image_members:
            return []

        candidates: dict[int, list[tuple[int, int, str]]] = defaultdict(list)
        current_question: int | None = None

        def picture_refs(
            element: ElementTree.Element,
        ) -> list[tuple[str, int, int]]:
            refs: list[tuple[str, int, int]] = []

            def walk(node: ElementTree.Element) -> None:
                if node.tag.rsplit("}", 1)[-1] == "endNote":
                    return
                if node.tag.rsplit("}", 1)[-1] == "pic":
                    image = next(
                        (
                            item
                            for item in node.iter()
                            if item.tag.rsplit("}", 1)[-1] == "img"
                            and item.attrib.get("binaryItemIDRef")
                        ),
                        None,
                    )
                    size = next(
                        (
                            item
                            for item in node.iter()
                            if item.tag.rsplit("}", 1)[-1] == "sz"
                        ),
                        None,
                    )
                    if image is not None:
                        refs.append((
                            str(image.attrib.get("binaryItemIDRef") or ""),
                            int((size.attrib.get("width") if size is not None else 0) or 0),
                            int((size.attrib.get("height") if size is not None else 0) or 0),
                        ))
                    return
                for child in node:
                    walk(child)

            walk(element)
            return refs

        section_names = sorted(
            (
                name
                for name in names
                if re.fullmatch(r"Contents/section\d+\.xml", name)
            ),
            key=lambda name: int(re.search(r"\d+", name).group(0)),
        )
        for section_name in section_names:
            try:
                root = ElementTree.fromstring(zf.read(section_name))
            except ElementTree.ParseError:
                continue
            for paragraph in root:
                if paragraph.tag.rsplit("}", 1)[-1] != "p":
                    continue
                end_note = next(
                    (
                        item
                        for item in paragraph.iter()
                        if item.tag.rsplit("}", 1)[-1] == "endNote"
                    ),
                    None,
                )
                if end_note is not None:
                    raw_number = str(end_note.attrib.get("number") or "")
                    current_question = (
                        int(raw_number)
                        if raw_number.isdigit() and int(raw_number) > 0
                        else None
                    )
                body = _hwpx_element_text(paragraph, skip_end_notes=True)
                if current_question and _HWPX_ANSWER_KEY_MARKER_RE.search(body):
                    current_question = None
                    continue
                if current_question is None:
                    continue
                for image_ref, width, height in picture_refs(paragraph):
                    member_name = image_members.get(image_ref)
                    if not member_name:
                        continue
                    # Tiny repeated glyphs are normally circled-choice or marker art.
                    if width and height and (width < 2500 or height < 1400):
                        continue
                    candidates[current_question].append((width, height, member_name))

        visuals: list[dict[str, object]] = []
        for question_number, question_candidates in sorted(candidates.items()):
            unique: dict[str, tuple[int, int, str]] = {}
            for candidate in question_candidates:
                unique.setdefault(candidate[2], candidate)
            if not unique:
                continue
            width, height, member_name = max(
                unique.values(),
                key=lambda candidate: max(1, candidate[0]) * max(1, candidate[1]),
            )
            raw = zf.read(member_name)
            mime, normalized = normalize_hwp_image_data(member_name, raw)
            width_px = 0
            height_px = 0
            try:
                from PIL import Image

                with Image.open(io.BytesIO(normalized)) as image:
                    width_px, height_px = image.size
            except Exception:
                pass
            visuals.append({
                "question_number": question_number,
                "source_member": member_name,
                "mime": mime,
                "data": normalized,
                "width_px": width_px,
                "height_px": height_px,
                "display_width": width,
                "display_height": height,
            })
        return visuals


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
        if category[0] == "C" or 0xE000 <= code <= 0xF8FF:
            chars.append(" ")
        else:
            chars.append(ch)
    cleaned = "".join(chars)
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _decode_hwp_para_text(payload: bytes) -> str:
    """Decode ParaText while removing the eight-code-unit inline controls."""
    source = payload.decode("utf-16le", "ignore")
    output: list[str] = []
    index = 0
    while index < len(source):
        character = source[index]
        code = ord(character)
        if code in {0x000A, 0x000D}:
            output.append("\n")
            index += 1
            continue
        if code == 0x0009:
            output.append("\t")
            index += 8 if index + 7 < len(source) and source[index + 7] == character else 1
            continue
        if code == 0x0018:
            output.append("-")
            index += 1
            continue
        if code == 0x001E:
            output.append("\N{NON-BREAKING HYPHEN}")
            index += 1
            continue
        if code == 0x001F:
            output.append("\N{NO-BREAK SPACE}")
            index += 1
            continue
        if code == 0x0011:
            output.extend(("\n", _HWP_ENDNOTE_ANCHOR, "\n"))
            index += 8 if index + 7 < len(source) and source[index + 7] == character else 1
            continue
        if 0 < code < 0x0020:
            index += 8 if index + 7 < len(source) and source[index + 7] == character else 1
            continue
        output.append(character)
        index += 1
    return _clean_hwp_text("".join(output))


def _rebuild_hwp_question_blocks(text_chunks: list[str]) -> str:
    if not any(_HWP_ENDNOTE_ANCHOR in chunk for chunk in text_chunks):
        return _clean_hwp_text("\n\n".join(text_chunks))

    prefix: list[str] = []
    blocks: list[str] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        lines = [
            line.strip()
            for line in normalize_space("\n".join(current)).splitlines()
            if line.strip() and line.strip() != "유형출제"
        ]
        if not lines:
            current = []
            return
        answer_index = next(
            (
                index
                for index, line in enumerate(lines)
                if re.match(r"^정답\s*[:：]?", line)
            ),
            None,
        )
        number = len(blocks) + 1
        if answer_index is None:
            blocks.append(f"{number}. {normalize_space(chr(10).join(lines))}")
            current = []
            return
        body_start = next(
            (
                index
                for index in range(answer_index + 1, len(lines))
                if _HWP_QUESTION_BODY_START_RE.match(lines[index])
            ),
            len(lines),
        )
        def preserve_internal_numbered_line(line: str) -> str:
            return f"· {line}" if re.match(r"^\d+\s*[.)]\s+", line) else line

        prompt_lines = [
            lines[0],
            *[
                preserve_internal_numbered_line(line)
                for line in lines[body_start:]
            ],
        ]
        explanation_lines = [
            preserve_internal_numbered_line(line)
            for line in lines[answer_index + 1:body_start]
        ]
        block_lines = [
            f"{number}. {normalize_space(chr(10).join(prompt_lines))}",
            lines[answer_index],
        ]
        explanation = normalize_space("\n".join(explanation_lines))
        if explanation:
            block_lines.extend(("해설:", explanation))
        blocks.append(normalize_space("\n".join(block_lines)))
        current = []

    for chunk in text_chunks:
        if _HWP_ENDNOTE_ANCHOR not in chunk:
            if current and _HWPX_ANSWER_KEY_MARKER_RE.search(chunk):
                flush()
                break
            if current:
                current.append(chunk)
            else:
                prefix.append(chunk)
            continue
        before, _marker, after = chunk.partition(_HWP_ENDNOTE_ANCHOR)
        flush()
        current = [before]
        if after.strip():
            current.append(after)
    flush()
    return normalize_space("\n\n".join([*prefix, *blocks]))


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
                text = _decode_hwp_para_text(payload)
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

    return _rebuild_hwp_question_blocks(text_chunks), images


def extract_hwp_text(data: bytes) -> str:
    text, _images = extract_hwp_text_and_images(data, include_images=False)
    return text
