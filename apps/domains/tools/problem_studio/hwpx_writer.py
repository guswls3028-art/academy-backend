from __future__ import annotations

from copy import deepcopy
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
_CHEMICAL_ELEMENT_SYMBOLS = frozenset(
    (
        "H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe "
        "Co Ni Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In "
        "Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf "
        "Ta W Re Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm "
        "Bk Cf Es Fm Md No Lr Rf Db Sg Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og"
    ).split()
)
_ROMAN_FUNCTION_NAMES = (
    "sin",
    "cos",
    "tan",
    "log",
    "ln",
    "lim",
    "max",
    "min",
    "exp",
)
_UNICODE_FORMULA_RE = re.compile(
    rf"(?<![A-Za-z0-9])"
    rf"(?P<formula>[A-Za-z0-9()+\-·=]*[{_SUBSCRIPT_CHARS}{_SUPERSCRIPT_CHARS}]"
    rf"[A-Za-z0-9()+\-·={_SUBSCRIPT_CHARS}{_SUPERSCRIPT_CHARS}]*)"
    rf"(?![A-Za-z0-9])"
)


def _is_chemical_formula(value: str) -> bool:
    if not any(
        character in _SUBSCRIPT_CHARS or character in _SUPERSCRIPT_CHARS
        for character in value
    ):
        return False
    letters = "".join(character for character in value if character.isalpha())
    symbols = re.findall(r"[A-Z][a-z]?", letters)
    return bool(
        symbols
        and "".join(symbols) == letters
        and all(symbol in _CHEMICAL_ELEMENT_SYMBOLS for symbol in symbols)
    )


@dataclass(frozen=True)
class DocumentStyle:
    title_font_family: str = "함초롬돋움"
    body_font_family: str = "함초롬바탕"
    title_size_pt: float = 20.0
    body_size_pt: float = 10.5
    body_width_ratio_percent: int = 100
    body_letter_spacing_percent: int = 0
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
            body_width_ratio_percent=int(
                value.get("body_width_ratio_percent", cls.body_width_ratio_percent)
            ),
            body_letter_spacing_percent=int(
                value.get(
                    "body_letter_spacing_percent",
                    cls.body_letter_spacing_percent,
                )
            ),
            line_spacing_percent=int(
                value.get("line_spacing_percent", cls.line_spacing_percent)
            ),
            question_spacing_pt=float(
                value.get("question_spacing_pt", cls.question_spacing_pt)
            ),
            native_equations=bool(value.get("native_equations", True)),
        )


@dataclass(frozen=True)
class PageLayout:
    page_width_mm: float = 210.0
    page_height_mm: float = 297.0
    margin_top_mm: float = 12.0
    margin_bottom_mm: float = 12.0
    margin_left_mm: float = 12.0
    margin_right_mm: float = 12.0
    column_count: int = 1
    column_gap_mm: float = 8.0
    center_line: bool = False
    center_line_style: str = "DASH"

    @classmethod
    def from_mapping(cls, value: Any) -> "PageLayout":
        raw = value.get("page_layout") if isinstance(value, dict) else None
        if not isinstance(raw, dict):
            return cls()
        return cls(
            page_width_mm=float(raw.get("page_width_mm", cls.page_width_mm)),
            page_height_mm=float(raw.get("page_height_mm", cls.page_height_mm)),
            margin_top_mm=float(raw.get("margin_top_mm", cls.margin_top_mm)),
            margin_bottom_mm=float(raw.get("margin_bottom_mm", cls.margin_bottom_mm)),
            margin_left_mm=float(raw.get("margin_left_mm", cls.margin_left_mm)),
            margin_right_mm=float(raw.get("margin_right_mm", cls.margin_right_mm)),
            column_count=2 if int(raw.get("column_count", 1)) == 2 else 1,
            column_gap_mm=float(raw.get("column_gap_mm", cls.column_gap_mm)),
            center_line=bool(raw.get("center_line", False)),
            center_line_style=(
                str(raw.get("center_line_style", cls.center_line_style)).upper()
                if str(raw.get("center_line_style", cls.center_line_style)).upper()
                in {"SOLID", "DASH", "DOT"}
                else cls.center_line_style
            ),
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
    package_manifest_tree = document.package.manifest_tree()
    package_manifest = package_manifest_tree.find(f"{{{_OPF_NAMESPACE}}}manifest")
    root_document = getattr(document, "_root", None)
    root_manifest_tree = getattr(root_document, "_manifest", None)
    root_manifest = (
        root_manifest_tree.find(f"{{{_OPF_NAMESPACE}}}manifest")
        if root_manifest_tree is not None
        else None
    )
    if package_manifest is None or root_manifest is None:
        raise RuntimeError("HWPX content manifest is missing.")

    item_tag = f"{{{_OPF_NAMESPACE}}}item"
    if not any(
        item.get("href", "").endswith("version.xml")
        for item in package_manifest.findall(item_tag)
    ):
        etree.SubElement(
            package_manifest,
            item_tag,
            {"id": "version", "href": _VERSION_HREF, "media-type": "application/xml"},
        )

    # HwpxDocument keeps its own manifest tree while package.add_image() writes
    # BinData entries to the package tree. Adding a section separates those two
    # trees, so a later document serialization could otherwise discard every
    # image entry except the first. Merge package-owned parts back into the
    # document tree before serialization.
    existing_ids = {item.get("id") for item in root_manifest.findall(item_tag)}
    for item in package_manifest.findall(item_tag):
        item_id = item.get("id")
        if item_id and item_id not in existing_ids:
            root_manifest.append(deepcopy(item))
            existing_ids.add(item_id)
    root_document._manifest_dirty = True


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


def _apply_run_metrics(
    document: HwpxDocument,
    *,
    char_pr_id: str,
    width_ratio_percent: int,
    letter_spacing_percent: int,
) -> None:
    header = document.headers[0]
    char_pr = next(
        (
            element
            for element in header.element.findall(f".//{{{_HH_NAMESPACE}}}charPr")
            if element.get("id") == str(char_pr_id)
        ),
        None,
    )
    if char_pr is None:
        raise RuntimeError("HWPX character style is missing.")
    language_keys = ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user")
    ratio = char_pr.find(f"{{{_HH_NAMESPACE}}}ratio")
    if ratio is None:
        ratio = etree.SubElement(char_pr, f"{{{_HH_NAMESPACE}}}ratio")
    spacing = char_pr.find(f"{{{_HH_NAMESPACE}}}spacing")
    if spacing is None:
        spacing = etree.SubElement(char_pr, f"{{{_HH_NAMESPACE}}}spacing")
    for key in language_keys:
        ratio.set(key, str(width_ratio_percent))
        spacing.set(key, str(letter_spacing_percent))
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
    script = " ".join(part for part in parts if part).strip()
    return f"{{rm {script}}}" if _is_chemical_formula(value) else script


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
        r"\,": " ",
        r"\;": " ",
        r"\quad": " ",
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
        r"\sin": "{rm sin}",
        r"\cos": "{rm cos}",
        r"\tan": "{rm tan}",
        r"\log": "{rm log}",
        r"\ln": "{rm ln}",
        r"\lim": "{rm lim}",
        r"\max": "{rm max}",
        r"\min": "{rm min}",
        r"\exp": "{rm exp}",
    }
    for source, target in replacements.items():
        script = script.replace(source, target)
    script = re.sub(r"\\mathrm\s*{([^{}]+)}", r"{rm \1}", script)
    script = re.sub(r"\\text\s*{([^{}]+)}", r"{rm \1}", script)
    script = re.sub(r"\\operatorname\s*{([^{}]+)}", r"{rm \1}", script)
    script = re.sub(r"\\([A-Za-z]+)", r"\1", script)
    function_names = "|".join(_ROMAN_FUNCTION_NAMES)
    script = re.sub(
        rf"(?<!rm )\b({function_names})\b",
        r"{rm \1}",
        script,
    )
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


def _mm_to_hwpunit(value: float) -> int:
    return max(0, round(float(value) * 7200 / 25.4))


def _fit_title_size_pt(
    title: str,
    *,
    requested_size_pt: float,
    page_layout: PageLayout,
    body_size_pt: float,
) -> float:
    if page_layout.column_count != 2:
        return requested_size_pt
    content_width_mm = (
        page_layout.page_width_mm
        - page_layout.margin_left_mm
        - page_layout.margin_right_mm
        - page_layout.column_gap_mm
    )
    column_width_pt = max(1.0, content_width_mm / 2 * 72.0 / 25.4)
    weighted_chars = sum(
        0.45 if char.isspace() else 0.62 if char.isascii() else 1.0
        for char in title
    )
    fitted_size = column_width_pt / max(1.0, weighted_chars * 0.72)
    minimum_size = max(10.0, min(requested_size_pt, body_size_pt + 1.0))
    return round(max(minimum_size, min(requested_size_pt, fitted_size)), 1)


def _apply_page_layout(document: HwpxDocument, layout: PageLayout) -> None:
    _apply_page_layout_to_paragraph(document.paragraphs[0], layout)


def _apply_page_layout_to_paragraph(paragraph: Any, layout: PageLayout) -> None:
    page_pr = paragraph.element.find(f".//{{{_HP_NAMESPACE}}}pagePr")
    if page_pr is None:
        return
    # HWPX uses WIDELY/NARROWLY here, not PORTRAIT/LANDSCAPE. Hancom Hangul
    # falls back to a landscape page when it receives the unsupported values.
    page_pr.set(
        "landscape",
        "NARROWLY" if layout.page_width_mm > layout.page_height_mm else "WIDELY",
    )
    page_pr.set("width", str(_mm_to_hwpunit(layout.page_width_mm)))
    page_pr.set("height", str(_mm_to_hwpunit(layout.page_height_mm)))
    margin = page_pr.find(f"{{{_HP_NAMESPACE}}}margin")
    if margin is not None:
        margin.set("top", str(_mm_to_hwpunit(layout.margin_top_mm)))
        margin.set("bottom", str(_mm_to_hwpunit(layout.margin_bottom_mm)))
        margin.set("left", str(_mm_to_hwpunit(layout.margin_left_mm)))
        margin.set("right", str(_mm_to_hwpunit(layout.margin_right_mm)))
    paragraph.section.mark_dirty()


def _image_format(mime: str) -> str:
    return "jpg" if mime == "image/jpeg" else "png"


def _append_question_visual(
    document: HwpxDocument,
    *,
    visual: dict[str, Any],
    page_layout: PageLayout,
    section: Any | None = None,
    max_height_mm: float = 88.0,
    height_fraction: float = 0.32,
) -> None:
    data = visual.get("data")
    if not isinstance(data, bytes) or not data:
        return
    width_px = max(1, int(visual.get("width_px") or 1))
    height_px = max(1, int(visual.get("height_px") or 1))
    content_width_mm = (
        page_layout.page_width_mm
        - page_layout.margin_left_mm
        - page_layout.margin_right_mm
        - (page_layout.column_gap_mm if page_layout.column_count == 2 else 0)
    )
    available_width_mm = content_width_mm / page_layout.column_count
    available_height_mm = min(
        max_height_mm,
        page_layout.page_height_mm * height_fraction,
    )
    width_mm = max(20.0, available_width_mm)
    height_mm = width_mm * height_px / width_px
    if height_mm > available_height_mm:
        height_mm = available_height_mm
        width_mm = height_mm * width_px / height_px
    item_id = document.add_image(data, _image_format(str(visual.get("mime") or "")))
    paragraph = document.add_paragraph("", section=section)
    paragraph.add_picture(
        item_id,
        width=_mm_to_hwpunit(width_mm),
        height=_mm_to_hwpunit(height_mm),
        align="CENTER",
    )


def _append_document_paragraph(
    document: HwpxDocument,
    *,
    text: str,
    char_pr_id: str,
    base_unit: int,
    native_equations: bool,
    section: Any | None = None,
) -> tuple[Any, str]:
    paragraph = document.add_paragraph("", section=section)
    preview = _replace_paragraph_content(
        paragraph,
        text=text,
        char_pr_id=char_pr_id,
        base_unit=base_unit,
        native_equations=native_equations,
    )
    return paragraph, preview


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
        _apply_run_metrics(
            document,
            char_pr_id=body_style_id,
            width_ratio_percent=style.body_width_ratio_percent,
            letter_spacing_percent=style.body_letter_spacing_percent,
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


def build_hwpx_exam_document(
    *,
    title: str,
    meta_lines: list[str],
    items: list[dict[str, Any]],
    document_style: dict[str, Any] | None = None,
    solutions: bool = False,
    question_visuals: dict[int, dict[str, Any]] | None = None,
) -> bytes:
    """Build a Korean exam/solution sheet with source-sized pages and flowing columns."""

    style = DocumentStyle.from_mapping(document_style)
    page_layout = PageLayout.from_mapping(document_style)
    preview_paragraphs: list[str] = []
    question_paragraph_indexes: list[int] = []
    title_text = title.strip() or ("해설지" if solutions else "문제지")
    title_size_pt = _fit_title_size_pt(
        title_text,
        requested_size_pt=style.title_size_pt,
        page_layout=page_layout,
        body_size_pt=style.body_size_pt,
    )

    with HwpxDocument.new() as document:
        _ensure_font_face(document, style.title_font_family)
        _ensure_font_face(document, style.body_font_family)
        title_style_id = document.ensure_run_style(
            bold=True,
            font=style.title_font_family,
            size=title_size_pt,
        )
        body_style_id = document.ensure_run_style(
            font=style.body_font_family,
            size=style.body_size_pt,
        )
        heading_style_id = document.ensure_run_style(
            bold=True,
            font=style.body_font_family,
            size=style.body_size_pt,
        )
        for char_pr_id in (body_style_id, heading_style_id):
            _apply_run_metrics(
                document,
                char_pr_id=char_pr_id,
                width_ratio_percent=style.body_width_ratio_percent,
                letter_spacing_percent=style.body_letter_spacing_percent,
            )

        preview_paragraphs.append(
            _replace_paragraph_content(
                document.paragraphs[0],
                text=title_text,
                char_pr_id=title_style_id,
                base_unit=round(title_size_pt * 100),
                native_equations=False,
            )
        )
        for line in meta_lines:
            _paragraph, preview = _append_document_paragraph(
                document,
                text=str(line or ""),
                char_pr_id=body_style_id,
                base_unit=round(style.body_size_pt * 100),
                native_equations=False,
            )
            preview_paragraphs.append(preview)

        if page_layout.column_count == 2:
            column_control = document.add_paragraph("")
            column_control.add_column_definition(
                col_count=2,
                same_size=True,
                same_gap=_mm_to_hwpunit(page_layout.column_gap_mm),
                separator_type=(
                    page_layout.center_line_style
                    if page_layout.center_line
                    else None
                ),
                separator_width="0.4 mm" if page_layout.center_line else None,
                separator_color="#7A7A7A" if page_layout.center_line else None,
            )

        if not items:
            _paragraph, preview = _append_document_paragraph(
                document,
                text="자동으로 분리된 문항이 없습니다. 원본 및 OCR 검수표를 확인하세요.",
                char_pr_id=body_style_id,
                base_unit=round(style.body_size_pt * 100),
                native_equations=False,
            )
            preview_paragraphs.append(preview)

        for item in items:
            number = int(item.get("number") or 0)
            question_visual = (question_visuals or {}).get(number)
            if solutions:
                heading = f"{number}번 정답  {str(item.get('answer') or '검수 필요')}"
                body_lines = [
                    str(item.get("answer_check") or "").strip(),
                    str(item.get("explanation") or "검수 후 작성").strip(),
                ]
            else:
                heading = f"{number}."
                prompt = str(item.get("prompt") or "").strip()
                choices = [
                    str(choice).strip()
                    for choice in (item.get("choices") or [])
                    if str(choice).strip()
                ]
                body_lines = [prompt, *choices]

            _heading_paragraph, preview = _append_document_paragraph(
                document,
                text=heading,
                char_pr_id=heading_style_id,
                base_unit=round(style.body_size_pt * 100),
                native_equations=style.native_equations,
            )
            question_paragraph_indexes.append(len(document.paragraphs) - 1)
            preview_paragraphs.append(preview)
            if solutions and question_visual:
                _append_question_visual(
                    document,
                    visual=question_visual,
                    page_layout=page_layout,
                )
                preview_paragraphs.append(f"[{number}번 원본 그림·표]")
            for body_index, line in enumerate(body_lines):
                if not line:
                    continue
                for paragraph_line in normalize_space(line).splitlines():
                    _paragraph, preview = _append_document_paragraph(
                        document,
                        text=paragraph_line,
                        char_pr_id=body_style_id,
                        base_unit=round(style.body_size_pt * 100),
                        native_equations=style.native_equations,
                    )
                    preview_paragraphs.append(preview)
                if not solutions and body_index == 0 and question_visual:
                    _append_question_visual(
                        document,
                        visual=question_visual,
                        page_layout=page_layout,
                    )
                    preview_paragraphs.append(f"[{number}번 원본 그림·표]")

        _apply_page_layout(document, page_layout)
        document.set_paragraph_format(
            line_spacing_percent=style.line_spacing_percent,
        )
        document.set_paragraph_format(
            paragraph_index=0,
            spacing_after_pt=6,
            keep_with_next=True,
        )
        if question_paragraph_indexes:
            document.set_paragraph_format(
                paragraph_indexes=question_paragraph_indexes,
                spacing_before_pt=style.question_spacing_pt,
                spacing_after_pt=2,
                keep_with_next=True,
            )

        _ensure_version_manifest_reference(document)
        preview_text = normalize_space("\n".join(preview_paragraphs)) + "\n"
        document.package.set_part("Preview/PrvText.txt", preview_text.encode("utf-8"))
        return document.to_bytes()


def build_hwpx_editable_wrong_note_document(
    *,
    title: str,
    meta_lines: list[str],
    problem_pages: list[dict[str, Any]],
    solution_pages: list[dict[str, Any]],
) -> bytes:
    """Build a source-faithful wrong-note HWPX with editable annotation fields."""

    if not problem_pages:
        return build_hwpx_text_document(
            title=title,
            paragraphs=["모을 오답이 없습니다."],
        )

    style = DocumentStyle()
    page_layout = PageLayout(
        page_width_mm=210.0,
        page_height_mm=297.0,
        margin_top_mm=14.0,
        margin_bottom_mm=14.0,
        margin_left_mm=14.0,
        margin_right_mm=14.0,
    )
    problem_layout = PageLayout(
        page_width_mm=210.0,
        page_height_mm=297.0,
        margin_top_mm=16.0,
        margin_bottom_mm=16.0,
        margin_left_mm=16.0,
        margin_right_mm=16.0,
        column_count=2,
        column_gap_mm=8.0,
    )
    preview_lines: list[str] = []

    with HwpxDocument.new() as document:
        _ensure_font_face(document, style.title_font_family)
        _ensure_font_face(document, style.body_font_family)
        title_style_id = document.ensure_run_style(
            bold=True,
            font=style.title_font_family,
            size=20,
        )
        heading_style_id = document.ensure_run_style(
            bold=True,
            font=style.body_font_family,
            size=15,
        )
        body_style_id = document.ensure_run_style(
            font=style.body_font_family,
            size=10,
        )

        def append_text(section: Any, text: str) -> None:
            _paragraph, preview = _append_document_paragraph(
                document,
                text=text,
                char_pr_id=body_style_id,
                base_unit=1000,
                native_equations=False,
                section=section,
            )
            preview_lines.append(preview)

        cover_section = document.sections[0]
        cover_paragraph = cover_section.paragraphs[0]
        _apply_page_layout_to_paragraph(cover_paragraph, page_layout)
        preview_lines.append(
            _replace_paragraph_content(
                cover_paragraph,
                text=title.strip() or "오답노트",
                char_pr_id=title_style_id,
                base_unit=2000,
                native_equations=False,
            )
        )
        for line in meta_lines:
            append_text(cover_section, str(line or ""))
        append_text(
            cover_section,
            "문제와 선생님 해설 원본은 이미지로 보존되고, 제목·정답·메모 문단은 한글에서 편집할 수 있습니다.",
        )

        # Match the teacher's A4 newspaper layout: one intact source problem per
        # column, left then right, with an explicit column break between items.
        problem_section = document.add_section()
        first_problem_paragraph = problem_section.paragraphs[0]
        _apply_page_layout_to_paragraph(first_problem_paragraph, problem_layout)
        first_problem_paragraph.add_column_definition(
            col_count=2,
            same_size=True,
            same_gap=_mm_to_hwpunit(problem_layout.column_gap_mm),
        )
        for index, page in enumerate(problem_pages, start=1):
            if index == 1:
                heading_paragraph = first_problem_paragraph
                preview = _replace_paragraph_content(
                    heading_paragraph,
                    text=str(page.get("heading") or f"{index}번"),
                    char_pr_id=heading_style_id,
                    base_unit=1500,
                    native_equations=False,
                )
            else:
                heading_paragraph, preview = _append_document_paragraph(
                    document,
                    text=str(page.get("heading") or f"{index}번"),
                    char_pr_id=heading_style_id,
                    base_unit=1500,
                    native_equations=False,
                    section=problem_section,
                )
                heading_paragraph.element.set("columnBreak", "1")
                problem_section.mark_dirty()
            preview_lines.append(f"문제 {index}칸")
            preview_lines.append(preview)
            append_text(problem_section, str(page.get("subheading") or ""))
            visual = page.get("visual")
            if isinstance(visual, dict) and visual.get("data"):
                _append_question_visual(
                    document,
                    visual=visual,
                    page_layout=problem_layout,
                    section=problem_section,
                    max_height_mm=205.0,
                    height_fraction=0.72,
                )
                preview_lines.append(f"[{index}번 문제 원본 이미지]")
            else:
                append_text(problem_section, "등록된 문제 이미지가 없습니다.")
            append_text(problem_section, "내 풀이 메모: ")

        divider_section = document.add_section()
        _apply_page_layout_to_paragraph(divider_section.paragraphs[0], page_layout)
        preview_lines.append(
            _replace_paragraph_content(
                divider_section.paragraphs[0],
                text="정답 및 해설",
                char_pr_id=title_style_id,
                base_unit=2000,
                native_equations=False,
            )
        )
        append_text(
            divider_section,
            "문제 풀이를 마친 뒤 확인하세요. 정답과 추가 메모 문단은 편집할 수 있습니다.",
        )

        for index, page in enumerate(solution_pages, start=1):
            section = document.add_section()
            _apply_page_layout_to_paragraph(section.paragraphs[0], page_layout)
            preview_lines.append(f"해설 {index}쪽")
            preview_lines.append(
                _replace_paragraph_content(
                    section.paragraphs[0],
                    text=str(page.get("heading") or f"{index}번 정답 및 해설"),
                    char_pr_id=heading_style_id,
                    base_unit=1500,
                    native_equations=False,
                )
            )
            append_text(section, f"정답: {str(page.get('answer') or '미등록')}")
            visual = page.get("visual")
            if isinstance(visual, dict) and visual.get("data"):
                _append_question_visual(
                    document,
                    visual=visual,
                    page_layout=page_layout,
                    section=section,
                    max_height_mm=205.0,
                    height_fraction=0.78,
                )
                preview_lines.append(f"[{index}번 선생님 해설 원본 이미지]")
            else:
                append_text(section, "등록된 선생님 해설 이미지가 없습니다.")
            append_text(section, "추가 메모: ")

        _ensure_version_manifest_reference(document)
        document.package.set_part(
            "Preview/PrvText.txt",
            (normalize_space("\n".join(preview_lines)) + "\n").encode("utf-8"),
        )
        return document.to_bytes()


def build_hwpx_source_fidelity_document(
    *,
    title: str,
    source_pages: list[dict[str, Any]],
) -> bytes:
    """Build a page-for-page HWPX visual reference with exact source proportions."""

    if not source_pages:
        return build_hwpx_text_document(
            title=title,
            paragraphs=["보존할 PDF·스캔 원본 페이지가 없습니다."],
        )

    preview_lines = [title.strip() or "원본 충실 대조본"]
    with HwpxDocument.new() as document:
        for index, source_page in enumerate(source_pages):
            section = document.sections[0] if index == 0 else document.add_section()
            paragraph = section.paragraphs[0]
            width_pt = float(source_page.get("page_width_pt") or 0)
            height_pt = float(source_page.get("page_height_pt") or 0)
            width_mm = width_pt * 25.4 / 72.0
            height_mm = height_pt * 25.4 / 72.0
            if not (90 <= width_mm <= 500 and 90 <= height_mm <= 500):
                width_px = max(1, int(source_page.get("width_px") or 1))
                height_px = max(1, int(source_page.get("height_px") or 1))
                width_mm, height_mm = (
                    (297.0, 210.0)
                    if width_px > height_px
                    else (210.0, 297.0)
                )
            layout = PageLayout(
                page_width_mm=width_mm,
                page_height_mm=height_mm,
                margin_top_mm=0,
                margin_bottom_mm=0,
                margin_left_mm=0,
                margin_right_mm=0,
            )
            _apply_page_layout_to_paragraph(paragraph, layout)
            data = source_page.get("data")
            if not isinstance(data, bytes) or not data:
                continue
            item_id = document.add_image(
                data,
                _image_format(str(source_page.get("mime") or "")),
            )
            paragraph.add_picture(
                item_id,
                width=_mm_to_hwpunit(width_mm),
                height=_mm_to_hwpunit(height_mm),
                treat_as_char=False,
                text_wrap="IN_FRONT_OF_TEXT",
                pos_overrides={
                    "horzRelTo": "PAPER",
                    "vertRelTo": "PAPER",
                    "horzAlign": "LEFT",
                    "vertAlign": "TOP",
                    "horzOffset": 0,
                    "vertOffset": 0,
                    "allowOverlap": "1",
                },
            )
            preview_lines.append(
                f"{source_page.get('source_name') or '원본'} "
                f"{source_page.get('page_number') or index + 1}쪽"
            )

        _ensure_version_manifest_reference(document)
        document.package.set_part(
            "Preview/PrvText.txt",
            (normalize_space("\n".join(preview_lines)) + "\n").encode("utf-8"),
        )
        return document.to_bytes()
