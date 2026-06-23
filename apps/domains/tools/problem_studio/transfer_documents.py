from __future__ import annotations

import base64
import html
import io
import re
import struct
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from django.http import HttpResponse


TRANSFER_MAX_UPLOAD_BYTES = 120 * 1024 * 1024
TRANSFER_MAX_ZIP_MEMBERS = 300
TRANSFER_MAX_ZIP_UNCOMPRESSED_BYTES = 180 * 1024 * 1024
TRANSFER_PDF_PAGES_PER_DOC = 60
TRANSFER_PDF_RENDER_ZOOM = 1.45
TRANSFER_HWP_GALLERY_IMAGES_PER_ROW = 1
SUPPORTED_TRANSFER_SUFFIXES = {
    ".pdf",
    ".hwp",
    ".hwpx",
    ".docx",
    ".doc",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}


@dataclass
class TransferDocument:
    filename: str
    html: str
    source_name: str
    kind: str
    text_chars: int = 0
    image_count: int = 0
    page_count: int = 0
    warning: str | None = None


@dataclass
class TransferPackage:
    filename: str
    content_type: str
    data: bytes
    documents: list[TransferDocument] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def _safe_filename(value: str, *, default: str = "source") -> str:
    name = Path(value or default).name
    name = re.sub(r"[\\/:*?\"<>|]+", "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120] or default


def _doc_filename(source_name: str, suffix: str = ".doc") -> str:
    stem = Path(_safe_filename(source_name)).stem or "source"
    return f"{stem}_원본이관{suffix}"


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


def _data_url(data: bytes, mime: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _read_upload(uploaded: Any) -> tuple[str, bytes]:
    name = _safe_filename(str(getattr(uploaded, "name", "source") or "source"))
    size = int(getattr(uploaded, "size", 0) or 0)
    if size > TRANSFER_MAX_UPLOAD_BYTES:
        raise ValueError(f"{name} 파일 크기가 너무 큽니다.")
    data = uploaded.read()
    try:
        uploaded.seek(0)
    except Exception:
        pass
    if len(data) > TRANSFER_MAX_UPLOAD_BYTES:
        raise ValueError(f"{name} 파일 크기가 너무 큽니다.")
    return name, data


def _source_kind(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return {
        ".pdf": "PDF",
        ".hwp": "HWP",
        ".hwpx": "HWPX",
        ".docx": "DOCX",
        ".doc": "DOC",
        ".png": "이미지",
        ".jpg": "이미지",
        ".jpeg": "이미지",
        ".webp": "이미지",
        ".zip": "ZIP",
    }.get(suffix, "기타")


def _office_doc_shell(title: str, body: str, *, extra_style: str = "") -> str:
    return f"""<!doctype html>
<html lang="ko" xmlns:o="urn:schemas-microsoft-com:office:office">
<head>
  <meta charset="utf-8" />
  <title>{_escape(title)}</title>
  <style>
    @page {{ size: A4; margin: 14mm 13mm 16mm; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: #111827;
      font-family: "Malgun Gothic", "맑은 고딕", "Batang", "바탕", serif;
      font-size: 10.5pt;
      line-height: 1.55;
    }}
    h1 {{
      margin: 0 0 8mm;
      padding-bottom: 4mm;
      border-bottom: 1.2pt solid #111827;
      font-size: 18pt;
      line-height: 1.25;
    }}
    .transfer-meta {{
      margin: 0 0 8mm;
      padding: 3mm 4mm;
      border-left: 3pt solid #2563eb;
      background: #eff6ff;
      color: #1f2937;
      font-size: 9.5pt;
    }}
    .source-page {{
      page-break-after: always;
      break-after: page;
      margin: 0 0 8mm;
    }}
    .source-page:last-child {{
      page-break-after: auto;
      break-after: auto;
    }}
    .source-page img, .source-image {{
      display: block;
      width: 100%;
      height: auto;
      page-break-inside: avoid;
    }}
    .source-text {{
      white-space: pre-wrap;
      word-break: keep-all;
      overflow-wrap: anywhere;
    }}
    .warning {{
      margin: 6mm 0;
      padding: 3mm 4mm;
      border: 1pt solid #f59e0b;
      background: #fffbeb;
      color: #92400e;
      font-weight: 700;
    }}
    {extra_style}
  </style>
</head>
<body>
{body}
</body>
</html>"""


def _meta_block(title: str, source_name: str, detail: str) -> str:
    return f"""
  <h1>{_escape(title)}</h1>
  <div class="transfer-meta">
    <strong>원본 파일</strong> {_escape(source_name)}<br />
    <strong>이관 방식</strong> {_escape(detail)}<br />
    <strong>생성 시각</strong> {_escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}
  </div>
"""


def _image_transfer_doc(name: str, data: bytes) -> TransferDocument:
    mime = _mime_for_suffix(Path(name).suffix)
    body = (
        _meta_block("원본 이미지 이관", name, "이미지 원본 보존")
        + f'<img class="source-image" src="{_data_url(data, mime)}" alt="{_escape(name)}" />'
    )
    return TransferDocument(
        filename=_doc_filename(name),
        html=_office_doc_shell(name, body),
        source_name=name,
        kind=_source_kind(name),
        image_count=1,
    )


def _pdf_transfer_docs(name: str, data: bytes) -> list[TransferDocument]:
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # pragma: no cover - dependency is present in api image
        raise ValueError("PDF 렌더링 모듈을 사용할 수 없습니다.") from exc

    documents: list[TransferDocument] = []
    with fitz.open(stream=data, filetype="pdf") as pdf:
        page_count = int(pdf.page_count)
        for start in range(0, page_count, TRANSFER_PDF_PAGES_PER_DOC):
            end = min(start + TRANSFER_PDF_PAGES_PER_DOC, page_count)
            page_html: list[str] = [
                _meta_block(
                    "PDF 원본 페이지 이관",
                    name,
                    f"스캔/페이지 이미지 보존 · {start + 1}-{end}쪽 / 총 {page_count}쪽",
                )
            ]
            for index in range(start, end):
                page = pdf[index]
                matrix = fitz.Matrix(TRANSFER_PDF_RENDER_ZOOM, TRANSFER_PDF_RENDER_ZOOM)
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                try:
                    image_bytes = pix.tobytes("jpeg", jpg_quality=82)
                    mime = "image/jpeg"
                except TypeError:
                    image_bytes = pix.tobytes("png")
                    mime = "image/png"
                page_no = index + 1
                page_html.append(
                    f'<section class="source-page">'
                    f'<p><strong>{page_no}쪽</strong></p>'
                    f'<img src="{_data_url(image_bytes, mime)}" alt="{_escape(name)} {page_no}쪽" />'
                    f'</section>'
                )
            part_label = f"_part{(start // TRANSFER_PDF_PAGES_PER_DOC) + 1:02d}" if page_count > TRANSFER_PDF_PAGES_PER_DOC else ""
            filename = _doc_filename(f"{Path(name).stem}{part_label}.pdf")
            documents.append(TransferDocument(
                filename=filename,
                html=_office_doc_shell(f"{name} {start + 1}-{end}쪽", "\n".join(page_html)),
                source_name=name,
                kind="PDF",
                page_count=end - start,
                image_count=end - start,
            ))
    return documents


def _simple_text_transfer_doc(name: str, text: str, detail: str, warning: str | None = None) -> TransferDocument:
    warning_html = f'<div class="warning">{_escape(warning)}</div>' if warning else ""
    body = (
        _meta_block("문서 본문 이관", name, detail)
        + warning_html
        + f'<div class="source-text">{_escape(text or "추출된 본문이 없습니다.")}</div>'
    )
    return TransferDocument(
        filename=_doc_filename(name),
        html=_office_doc_shell(name, body),
        source_name=name,
        kind=_source_kind(name),
        text_chars=len(text or ""),
        warning=warning,
    )


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


def _extract_hwp_text_and_images(data: bytes) -> tuple[str, list[tuple[str, str, bytes]]]:
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

        for parts in streams:
            if len(parts) < 2 or parts[0] != "BinData":
                continue
            filename = parts[-1]
            suffix = Path(filename).suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}:
                continue
            image_data = ole.openstream(parts).read()
            images.append((filename, _mime_for_suffix(suffix), image_data))

    return _clean_hwp_text("\n\n".join(text_chunks)), images


def _fast_hwp_transfer_doc(name: str, data: bytes) -> TransferDocument:
    text, images = _extract_hwp_text_and_images(data)
    image_html = []
    for index, (image_name, mime, image_data) in enumerate(images, start=1):
        image_html.append(
            f'<figure class="hwp-image">'
            f'<img src="{_data_url(image_data, mime)}" alt="{_escape(image_name)}" />'
            f'<figcaption>{index}. {_escape(image_name)}</figcaption>'
            f'</figure>'
        )
    body = (
        _meta_block("HWP 원본 이관", name, "HWP 본문 텍스트 + 임베디드 이미지 전체 추출")
        + '<h2>본문 텍스트</h2>'
        + f'<div class="source-text">{_escape(text or "추출된 본문 텍스트가 없습니다.")}</div>'
        + '<h2>그림/도표 이미지</h2>'
        + (
            f'<div class="hwp-gallery">{"".join(image_html)}</div>'
            if image_html
            else '<p class="warning">추출된 이미지가 없습니다.</p>'
        )
    )
    return TransferDocument(
        filename=_doc_filename(name),
        html=_office_doc_shell(
            name,
            body,
            extra_style=f"""
    h2 {{ margin: 10mm 0 4mm; font-size: 13pt; }}
    .hwp-gallery {{ display: grid; grid-template-columns: repeat({TRANSFER_HWP_GALLERY_IMAGES_PER_ROW}, minmax(0, 1fr)); gap: 5mm; }}
    .hwp-image {{ margin: 0 0 5mm; page-break-inside: avoid; }}
    .hwp-image img {{ display: block; max-width: 100%; height: auto; }}
    .hwp-image figcaption {{ margin-top: 1.5mm; color: #6b7280; font-size: 8.5pt; text-align: right; }}
""",
        ),
        source_name=name,
        kind="HWP",
        text_chars=len(text),
        image_count=len(images),
    )


def _extract_hwpx_text(data: bytes) -> str:
    from apps.domains.tools.problem_studio.services import _extract_hwpx_text as extract_hwpx_text

    return extract_hwpx_text(data)


def _extract_docx_text(data: bytes) -> str:
    from apps.domains.tools.problem_studio.services import _extract_docx_text as extract_docx_text

    return extract_docx_text(data)


def _docs_from_named_bytes(name: str, data: bytes) -> tuple[list[TransferDocument], list[str]]:
    suffix = Path(name).suffix.lower()
    warnings: list[str] = []
    try:
        if suffix == ".pdf":
            return _pdf_transfer_docs(name, data), warnings
        if suffix == ".hwp":
            return [_fast_hwp_transfer_doc(name, data)], warnings
        if suffix == ".hwpx":
            text = _extract_hwpx_text(data)
            return [_simple_text_transfer_doc(name, text, "HWPX 본문 텍스트 이관")], warnings
        if suffix == ".docx":
            text = _extract_docx_text(data)
            return [_simple_text_transfer_doc(name, text, "DOCX 본문 텍스트 이관")], warnings
        if suffix == ".doc":
            warning = "DOC 바이너리는 직접 렌더링하지 못해 원본 등록 리포트만 생성했습니다. DOCX/PDF로 저장하면 본문 이관이 가능합니다."
            return [_simple_text_transfer_doc(name, "", "DOC 원본 등록", warning)], [f"{name}: {warning}"]
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            return [_image_transfer_doc(name, data)], warnings
    except Exception as exc:
        warning = f"{name}: 원본 이관 중 오류가 발생했습니다. ({exc})"
        return [_simple_text_transfer_doc(name, "", "오류 리포트", warning)], [warning]
    warning = f"{name}: 지원하지 않는 파일 형식입니다."
    return [_simple_text_transfer_doc(name, "", "지원하지 않는 파일", warning)], [warning]


def _expand_zip(name: str, data: bytes) -> tuple[list[tuple[str, bytes]], list[str]]:
    warnings: list[str] = []
    members: list[tuple[str, bytes]] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        infos = [info for info in zf.infolist() if not info.is_dir()]
        if len(infos) > TRANSFER_MAX_ZIP_MEMBERS:
            raise ValueError(f"{name} 내부 파일 수가 너무 많습니다.")
        total = sum(max(0, int(info.file_size or 0)) for info in infos)
        if total > TRANSFER_MAX_ZIP_UNCOMPRESSED_BYTES:
            raise ValueError(f"{name} 내부 용량이 너무 큽니다.")
        for info in infos:
            suffix = Path(info.filename).suffix.lower()
            if suffix not in SUPPORTED_TRANSFER_SUFFIXES:
                continue
            safe_member_name = _safe_filename(Path(info.filename).name)
            if not safe_member_name:
                continue
            members.append((safe_member_name, zf.read(info)))
        if not members:
            warnings.append(f"{name}: ZIP 안에서 지원하는 문서 파일을 찾지 못했습니다.")
    return members, warnings


def build_transfer_package(*, payload: dict[str, Any], source_files: Iterable[Any]) -> TransferPackage:
    title = str(payload.get("title") or "문제 제작 원본 이관")
    documents: list[TransferDocument] = []
    warnings: list[str] = []

    for uploaded in source_files:
        name, data = _read_upload(uploaded)
        if Path(name).suffix.lower() == ".zip":
            try:
                members, zip_warnings = _expand_zip(name, data)
                warnings.extend(zip_warnings)
            except Exception as exc:
                warning = f"{name}: ZIP 해제 중 오류가 발생했습니다. ({exc})"
                documents.append(_simple_text_transfer_doc(name, "", "ZIP 오류 리포트", warning))
                warnings.append(warning)
                continue
            for member_name, member_data in members:
                docs, doc_warnings = _docs_from_named_bytes(member_name, member_data)
                documents.extend(docs)
                warnings.extend(doc_warnings)
            continue

        docs, doc_warnings = _docs_from_named_bytes(name, data)
        documents.extend(docs)
        warnings.extend(doc_warnings)

    if not documents:
        warning = "이관할 원본 파일이 없습니다."
        documents.append(_simple_text_transfer_doc("empty.txt", "", "빈 요청", warning))
        warnings.append(warning)

    now = datetime.now().strftime("%Y%m%d-%H%M%S")
    package_name = f"{_safe_filename(title, default='problem-studio')}_원본이관_{now}.zip"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        used_names: set[str] = set()
        for index, doc in enumerate(documents, start=1):
            base_name = _safe_filename(doc.filename)
            zip_name = f"{index:02d}_{base_name}"
            while zip_name in used_names:
                zip_name = f"{index:02d}_{Path(base_name).stem}_{len(used_names) + 1}.doc"
            used_names.add(zip_name)
            zf.writestr(zip_name, "\ufeff" + doc.html)
        zf.writestr("00_변환리포트.html", _build_report_html(title, documents, warnings))

    return TransferPackage(
        filename=package_name,
        content_type="application/zip",
        data=buffer.getvalue(),
        documents=documents,
        warnings=warnings,
    )


def _build_report_html(title: str, documents: list[TransferDocument], warnings: list[str]) -> str:
    rows = "\n".join(
        f"<tr>"
        f"<td>{index}</td>"
        f"<td>{_escape(doc.source_name)}</td>"
        f"<td>{_escape(doc.filename)}</td>"
        f"<td>{_escape(doc.kind)}</td>"
        f"<td>{doc.page_count}</td>"
        f"<td>{doc.image_count}</td>"
        f"<td>{doc.text_chars}</td>"
        f"<td>{_escape(doc.warning or '')}</td>"
        f"</tr>"
        for index, doc in enumerate(documents, start=1)
    )
    warning_items = "\n".join(f"<li>{_escape(w)}</li>" for w in warnings) or "<li>경고 없음</li>"
    body = f"""
  <h1>{_escape(title)} 변환 리포트</h1>
  <div class="transfer-meta">
    <strong>생성 시각</strong> {_escape(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))}<br />
    <strong>문서 수</strong> {len(documents)}개<br />
    <strong>이미지/페이지 수</strong> {sum(doc.image_count for doc in documents)}개
  </div>
  <h2>변환 결과</h2>
  <table>
    <thead>
      <tr><th>#</th><th>원본</th><th>산출물</th><th>유형</th><th>쪽수</th><th>이미지</th><th>본문 글자</th><th>경고</th></tr>
    </thead>
    <tbody>{rows}</tbody>
  </table>
  <h2>경고</h2>
  <ul>{warning_items}</ul>
"""
    return _office_doc_shell(
        f"{title} 변환 리포트",
        body,
        extra_style="""
    table { width: 100%; border-collapse: collapse; margin: 5mm 0; }
    th, td { border: 0.5pt solid #cbd5e1; padding: 2mm; vertical-align: top; font-size: 9pt; }
    th { background: #eef2ff; }
""",
    )


def package_to_response(package: TransferPackage) -> HttpResponse:
    response = HttpResponse(package.data, content_type=package.content_type)
    fallback = package.filename.encode("ascii", "ignore").decode("ascii") or "problem-studio-transfer.zip"
    response["Content-Disposition"] = f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(package.filename)}"
    response["X-Problem-Studio-Document-Count"] = str(len(package.documents))
    response["X-Problem-Studio-Warning-Count"] = str(len(package.warnings))
    return response
