from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable
from uuid import uuid4

from hwpx import HwpxDocument
from lxml import etree

from apps.domains.tools.problem_studio.structure import normalize_space


_OPF_NAMESPACE = "http://www.idpf.org/2007/opf/"
_HH_NAMESPACE = "http://www.hancom.co.kr/hwpml/2011/head"
_HP_NAMESPACE = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_VERSION_HREF = "../version.xml"
_QUESTION_HEADER_RE = re.compile(r"^\d+\.\s+(?:문제|개념)\s*/")
_MARKED_EQUATION_RE = re.compile(
    r"\[\[수식:(?P<tagged>.+?)]]|(?<!\\)\$(?P<dollar>[^$\n]+)\$|\\\((?P<paren>.+?)\\\)"
)
_SUBSCRIPT_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎", "0123456789+-=()")
_SUPERSCRIPT_MAP = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ", "0123456789+-=()n")
_SUBSCRIPT_CHARS = "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎"
_SUPERSCRIPT_CHARS = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿ"
_UNICODE_FORMULA_RE = re.compile(
    rf"(?<![A-Za-z0-9])"
    rf"(?P<formula>[A-Za-z0-9()+\-·=]*[{_SUBSCRIPT_CHARS}{_SUPERSCRIPT_CHARS}]"
    rf"[A-Za-z0-9()+\-·={_SUBSCRIPT_CHARS}{_SUPERSCRIPT_CHARS}]*)"
    rf"(?![A-Za-z0-9])"
)


@dataclass(frozen=True)
class DocumentStyle:
    title_font_family: str = "함초롬돋움"
    body_font_family: str = "함초롬바탕"
    title_size_pt: float = 20.0
    body_size_pt: float = 10.5
    line_spacing_percent: int = 155
    question_spacing_pt: float = 10.0
    native_equations: bool = True

    @classmethod
    def from_mapping(cls, value: Any) -> "DocumentStyle":
        if not isinstance(value, dict):
            return cls()
        title_font = value.get("title_font")
        body_font = value.get("body_font")
        return cls(
            title_font_family=str(
                title_font.get("family_name")
                if isinstance(title_font, dict)
                else cls.title_font_family
            )[:160],
            body_font_family=str(
                body_font.get("family_name")
                if isinstance(body_font, dict)
                else cls.body_font_family
            )[:160],
            title_size_pt=float(value.get("title_size_pt", cls.title_size_pt)),
            body_size_pt=float(value.get("body_size_pt", cls.body_size_pt)),
            line_spacing_percent=int(
                value.get("line_spacing_percent", cls.line_spacing_percent)
            ),
            question_spacing_pt=float(
                value.get("question_spacing_pt", cls.question_spacing_pt)
            ),
            native_equations=bool(value.get("native_equations", True)),
        )


@dataclass(frozen=True)
class InlineSegment:
    kind: str
    value: str
    preview: str


def _split_paragraphs(title: str, paragraphs: list[str]) -> list[str]:
    output = [title.strip()] if title.strip() else []
    for paragraph in paragraphs:
        normalized = normalize_space(paragraph)
        if not normalized:
            output.append("")
            continue
        output.extend(line.strip() for line in normalized.splitlines())
    return output or ["문제 검수본"]


def _ensure_version_manifest_reference(document: HwpxDocument) -> None:
    manifest_tree = document.package.manifest_tree()
    manifest = manifest_tree.find(f"{{{_OPF_NAMESPACE}}}manifest")
    if manifest is None:
        raise RuntimeError("HWPX content manifest is missing.")

    item_tag = f"{{{_OPF_NAMESPACE}}}item"
    if not any(item.get("href", "").endswith("version.xml") for item in manifest.findall(item_tag)):
        etree.SubElement(
            manifest,
            item_tag,
            {"id": "version", "href": _VERSION_HREF, "media-type": "application/xml"},
        )
        document.package.set_xml(document.package.MANIFEST_PATH, manifest_tree)


def _ensure_font_face(document: HwpxDocument, family_name: str) -> None:
    family = family_name.strip()
    if not family:
        return
    header = document.headers[0]
    font_tag = f"{{{_HH_NAMESPACE}}}font"
    type_info_tag = f"{{{_HH_NAMESPACE}}}typeInfo"
    for fontface in header.element.findall(f".//{{{_HH_NAMESPACE}}}fontface"):
        if any(font.get("face") == family for font in fontface.findall(font_tag)):
            continue
        ids = []
        for font in fontface.findall(font_tag):
            try:
                ids.append(int(font.get("id", "0")))
            except ValueError:
                continue
        font_id = str(max(ids, default=-1) + 1)
        font = etree.SubElement(
            fontface,
            font_tag,
            {
                "id": font_id,
                "face": family,
                "type": "TTF",
                "isEmbedded": "0",
            },
        )
        etree.SubElement(
            font,
            type_info_tag,
            {
                "familyType": "FCAT_GOTHIC",
                "weight": "6",
                "proportion": "4",
                "contrast": "0",
                "strokeVariation": "1",
                "armStyle": "1",
                "letterform": "1",
                "midline": "1",
                "xHeight": "1",
            },
        )
        fontface.set("fontCnt", str(len(fontface.findall(font_tag))))
    header.mark_dirty()


def _unicode_formula_to_eqedit(value: str) -> str:
    parts: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character in _SUBSCRIPT_CHARS:
            end = index + 1
            while end < len(value) and value[end] in _SUBSCRIPT_CHARS:
                end += 1
            decoded = value[index:end].translate(_SUBSCRIPT_MAP)
            parts.append(f"_ {{{' '.join(decoded)}}}")
            index = end
            continue
        if character in _SUPERSCRIPT_CHARS:
            end = index + 1
            while end < len(value) and value[end] in _SUPERSCRIPT_CHARS:
                end += 1
            decoded = value[index:end].translate(_SUPERSCRIPT_MAP)
            parts.append(f"^ {{{' '.join(decoded)}}}")
            index = end
            continue
        parts.append(character)
        index += 1
    return " ".join(part for part in parts if part).strip()


def _balanced_group(value: str, start: int) -> tuple[str, int] | None:
    if start >= len(value) or value[start] != "{":
        return None
    depth = 0
    for index in range(start, len(value)):
        if value[index] == "{":
            depth += 1
        elif value[index] == "}":
            depth -= 1
            if depth == 0:
                return value[start + 1:index], index + 1
    return None


def _replace_latex_fractions(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        if value.startswith(r"\frac", index):
            numerator = _balanced_group(value, index + 5)
            if numerator is not None:
                denominator = _balanced_group(value, numerator[1])
                if denominator is not None:
                    output.append(
                        "{ "
                        + _replace_latex_fractions(numerator[0])
                        + " } over { "
                        + _replace_latex_fractions(denominator[0])
                        + " }"
                    )
                    index = denominator[1]
                    continue
        output.append(value[index])
        index += 1
    return "".join(output)


def _latex_to_eqedit(value: str) -> str:
    script = _replace_latex_fractions(value.strip())
    replacements = {
        r"\left": "",
        r"\right": "",
        r"\sqrt": "sqrt",
        r"\cdot": "cdot",
        r"\times": "times",
        r"\div": "div",
        r"\leq": "LEQ",
        r"\le": "LEQ",
        r"\geq": "GEQ",
        r"\ge": "GEQ",
        r"\neq": "NEQ",
        r"\ne": "NEQ",
        r"\pi": "pi",
        r"\theta": "theta",
        r"\alpha": "alpha",
        r"\beta": "beta",
        r"\gamma": "gamma",
        r"\Delta": "DELTA",
        r"\delta": "delta",
    }
    for source, target in replacements.items():
        script = script.replace(source, target)
    script = re.sub(r"\\mathrm\s*{([^{}]+)}", r"\1", script)
    script = re.sub(r"\\text\s*{([^{}]+)}", r"\1", script)
    script = re.sub(r"\\([A-Za-z]+)", r"\1", script)
    return re.sub(r"\s+", " ", script).strip()


def _unicode_formula_segments(value: str) -> list[InlineSegment]:
    segments: list[InlineSegment] = []
    cursor = 0
    for match in _UNICODE_FORMULA_RE.finditer(value):
        if match.start() > cursor:
            plain = value[cursor:match.start()]
            segments.append(InlineSegment("text", plain, plain))
        formula = match.group("formula")
        segments.append(
            InlineSegment("equation", _unicode_formula_to_eqedit(formula), formula)
        )
        cursor = match.end()
    if cursor < len(value):
        plain = value[cursor:]
        segments.append(InlineSegment("text", plain, plain))
    return segments or [InlineSegment("text", value, value)]


def _inline_segments(value: str, *, native_equations: bool) -> list[InlineSegment]:
    if not native_equations:
        return [InlineSegment("text", value, value)]
    segments: list[InlineSegment] = []
    cursor = 0
    for match in _MARKED_EQUATION_RE.finditer(value):
        if match.start() > cursor:
            segments.extend(_unicode_formula_segments(value[cursor:match.start()]))
        raw = match.group("tagged") or match.group("dollar") or match.group("paren") or ""
        script = _latex_to_eqedit(raw)
        if script and len(script) <= 2000:
            segments.append(InlineSegment("equation", script, raw))
        else:
            original = match.group(0)
            segments.append(InlineSegment("text", original, original))
        cursor = match.end()
    if cursor < len(value):
        segments.extend(_unicode_formula_segments(value[cursor:]))
    return segments or [InlineSegment("text", value, value)]


def _append_equation(run: Any, *, script: str, base_unit: int) -> None:
    for child in list(run.element):
        if etree.QName(child).localname == "t" and not "".join(child.itertext()):
            run.element.remove(child)
    equation = etree.SubElement(
        run.element,
        f"{{{_HP_NAMESPACE}}}equation",
        {
            "id": str(uuid4().int & 0x7FFFFFFF),
            "zOrder": "0",
            "numberingType": "EQUATION",
            "textWrap": "TOP_AND_BOTTOM",
            "textFlow": "BOTH_SIDES",
            "lock": "0",
            "dropcapstyle": "None",
            "version": "Equation Version 60",
            "baseLine": "0",
            "textColor": "#000000",
            "baseUnit": str(base_unit),
            "lineMode": "CHAR",
            "font": "HancomEQN",
        },
    )
    etree.SubElement(
        equation,
        f"{{{_HP_NAMESPACE}}}sz",
        {
            "width": "0",
            "widthRelTo": "ABSOLUTE",
            "height": "0",
            "heightRelTo": "ABSOLUTE",
            "protect": "0",
        },
    )
    etree.SubElement(
        equation,
        f"{{{_HP_NAMESPACE}}}pos",
        {
            "treatAsChar": "1",
            "affectLSpacing": "0",
            "flowWithText": "1",
            "allowOverlap": "0",
            "holdAnchorAndSO": "0",
            "vertRelTo": "PARA",
            "horzRelTo": "PARA",
            "vertAlign": "TOP",
            "horzAlign": "LEFT",
            "vertOffset": "0",
            "horzOffset": "0",
        },
    )
    etree.SubElement(
        equation,
        f"{{{_HP_NAMESPACE}}}outMargin",
        {"left": "56", "right": "56", "top": "0", "bottom": "0"},
    )
    comment = etree.SubElement(equation, f"{{{_HP_NAMESPACE}}}shapeComment")
    comment.text = "편집 가능한 한글 수식입니다."
    script_element = etree.SubElement(equation, f"{{{_HP_NAMESPACE}}}script")
    script_element.text = script
    run.paragraph.section.mark_dirty()


def _replace_paragraph_content(
    paragraph: Any,
    *,
    text: str,
    char_pr_id: str,
    base_unit: int,
    native_equations: bool,
) -> str:
    for child in list(paragraph.element):
        if etree.QName(child).localname != "run":
            continue
        structural_run = any(
            etree.QName(descendant).localname in {"secPr", "ctrl"}
            for descendant in child.iterdescendants()
        )
        if not structural_run:
            paragraph.element.remove(child)
    preview_parts: list[str] = []
    for segment in _inline_segments(text, native_equations=native_equations):
        preview_parts.append(segment.preview)
        run = paragraph.add_run("", char_pr_id_ref=char_pr_id)
        if segment.kind == "equation":
            _append_equation(run, script=segment.value, base_unit=base_unit)
        else:
            run.text = segment.value
    paragraph.section.mark_dirty()
    return "".join(preview_parts)


def _iter_custom_question_paragraphs(paragraphs: Iterable[str]) -> list[int]:
    return [
        index
        for index, paragraph in enumerate(paragraphs)
        if index > 0 and _QUESTION_HEADER_RE.match(paragraph)
    ]


def build_hwpx_text_document(
    *,
    title: str,
    paragraphs: list[str],
    document_style: dict[str, Any] | None = None,
) -> bytes:
    """Build an HWPX with reusable typography and native editable equations."""

    paragraph_list = _split_paragraphs(title, paragraphs)
    style = DocumentStyle.from_mapping(document_style)
    preview_paragraphs: list[str] = []

    with HwpxDocument.new() as document:
        _ensure_font_face(document, style.title_font_family)
        _ensure_font_face(document, style.body_font_family)
        title_style_id = document.ensure_run_style(
            bold=True,
            font=style.title_font_family,
            size=style.title_size_pt,
        )
        body_style_id = document.ensure_run_style(
            font=style.body_font_family,
            size=style.body_size_pt,
        )

        preview_paragraphs.append(
            _replace_paragraph_content(
                document.paragraphs[0],
                text=paragraph_list[0],
                char_pr_id=title_style_id,
                base_unit=round(style.title_size_pt * 100),
                native_equations=False,
            )
        )
        for paragraph_text in paragraph_list[1:]:
            paragraph = document.add_paragraph("")
            preview_paragraphs.append(
                _replace_paragraph_content(
                    paragraph,
                    text=paragraph_text,
                    char_pr_id=body_style_id,
                    base_unit=round(style.body_size_pt * 100),
                    native_equations=style.native_equations,
                )
            )

        document.set_paragraph_format(
            line_spacing_percent=style.line_spacing_percent,
        )
        document.set_paragraph_format(
            paragraph_index=0,
            spacing_after_pt=8,
            keep_with_next=True,
        )
        question_indexes = _iter_custom_question_paragraphs(paragraph_list)
        if question_indexes:
            document.set_paragraph_format(
                paragraph_indexes=question_indexes,
                spacing_before_pt=style.question_spacing_pt,
                keep_with_next=True,
            )

        _ensure_version_manifest_reference(document)
        preview_text = normalize_space("\n".join(preview_paragraphs)) + "\n"
        document.package.set_part("Preview/PrvText.txt", preview_text.encode("utf-8"))
        return document.to_bytes()
