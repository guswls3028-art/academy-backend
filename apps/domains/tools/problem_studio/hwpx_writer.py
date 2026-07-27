from __future__ import annotations

from hwpx import HwpxDocument

from apps.domains.tools.problem_studio.structure import normalize_space


def _split_paragraphs(title: str, paragraphs: list[str]) -> list[str]:
    output = [title.strip()] if title.strip() else []
    for paragraph in paragraphs:
        normalized = normalize_space(paragraph)
        if not normalized:
            output.append("")
            continue
        output.extend(line.strip() for line in normalized.splitlines())
    return output or ["문제 검수본"]


def build_hwpx_text_document(*, title: str, paragraphs: list[str]) -> bytes:
    """Build a text-focused HWPX document from an editor-compatible skeleton."""

    paragraph_list = _split_paragraphs(title, paragraphs)
    preview_text = normalize_space("\n".join(paragraph_list)) + "\n"

    with HwpxDocument.new() as document:
        document.paragraphs[0].text = paragraph_list[0]
        for paragraph in paragraph_list[1:]:
            document.add_paragraph(paragraph)
        document.package.set_part("Preview/PrvText.txt", preview_text.encode("utf-8"))
        return document.to_bytes()
