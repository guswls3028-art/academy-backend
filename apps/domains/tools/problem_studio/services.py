from __future__ import annotations

import json
import logging
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Iterable
from xml.etree import ElementTree

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 80 * 1024 * 1024
MAX_TEXT_CHARS = 18_000
MAX_OUTPUT_QUESTIONS = 40
MAX_VARIANT_COUNT = 10
MAX_ZIP_UNCOMPRESSED_BYTES = 25 * 1024 * 1024

SUPPORTED_TEXT_SUFFIXES = (".pdf", ".hwpx", ".docx")


@dataclass(frozen=True)
class SourceExtraction:
    name: str
    kind: str
    size_label: str
    extracted_text: str
    warning: str | None = None


def _size_label(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / 1024 / 1024:.1f}MB"
    if size >= 1024:
        return f"{(size + 1023) // 1024}KB"
    return f"{size}B"


def _source_kind(filename: str) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        return "PDF"
    if name.endswith(".hwpx"):
        return "HWPX"
    if name.endswith(".hwp"):
        return "HWP"
    if name.endswith(".docx"):
        return "DOCX"
    if name.endswith(".doc"):
        return "DOC"
    return "기타"


def _normalize_space(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.replace("\r", "\n").split("\n")]
    compact: list[str] = []
    for line in lines:
        if line:
            compact.append(line)
        elif compact and compact[-1] != "":
            compact.append("")
    return "\n".join(compact).strip()


def _read_limited(uploaded: Any) -> bytes:
    size = int(getattr(uploaded, "size", 0) or 0)
    if size > MAX_UPLOAD_BYTES:
        raise ValueError(f"{getattr(uploaded, 'name', '파일')} 크기가 너무 큽니다.")
    data = uploaded.read()
    try:
        uploaded.seek(0)
    except Exception:
        pass
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"{getattr(uploaded, 'name', '파일')} 크기가 너무 큽니다.")
    return data


def _extract_pdf_text(data: bytes) -> str:
    try:
        import fitz  # PyMuPDF
    except Exception as exc:  # pragma: no cover - dependency is present in api image
        raise ValueError("PDF 텍스트 추출 모듈을 사용할 수 없습니다.") from exc

    chunks: list[str] = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            chunks.append(page.get_text("text") or "")
    return _normalize_space("\n\n".join(chunks))


def _safe_zip_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = zf.infolist()
    if len(members) > 400:
        raise ValueError("문서 내부 파일 수가 너무 많습니다.")
    total = sum(max(0, int(m.file_size or 0)) for m in members)
    if total > MAX_ZIP_UNCOMPRESSED_BYTES:
        raise ValueError("문서 내부 용량이 너무 큽니다.")
    return members


def _xml_text(xml_bytes: bytes) -> str:
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


def _extract_hwpx_text(data: bytes) -> str:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        members = _safe_zip_members(zf)
        names = [m.filename for m in members]
        if "Preview/PrvText.txt" in names:
            return _normalize_space(zf.read("Preview/PrvText.txt").decode("utf-8", "ignore"))
        section_names = sorted(
            name for name in names
            if name.startswith("Contents/") and name.lower().endswith(".xml")
        )
        chunks = [_xml_text(zf.read(name)) for name in section_names]
    return _normalize_space("\n".join(chunks))


def _extract_docx_text(data: bytes) -> str:
    with zipfile.ZipFile(BytesIO(data)) as zf:
        _safe_zip_members(zf)
        if "word/document.xml" not in zf.namelist():
            return ""
        return _normalize_space(_xml_text(zf.read("word/document.xml")))


def extract_source(uploaded: Any) -> SourceExtraction:
    name = str(getattr(uploaded, "name", "source"))
    data = _read_limited(uploaded)
    kind = _source_kind(name)
    warning: str | None = None
    text = ""

    try:
        lower = name.lower()
        if lower.endswith(".pdf"):
            text = _extract_pdf_text(data)
        elif lower.endswith(".hwpx"):
            text = _extract_hwpx_text(data)
        elif lower.endswith(".docx"):
            text = _extract_docx_text(data)
        elif lower.endswith(".hwp"):
            warning = "HWP 바이너리 파일은 현재 소스 등록만 지원합니다. HWPX/PDF로 저장하면 본문 추출까지 됩니다."
        elif lower.endswith(".doc"):
            warning = "DOC 파일은 현재 소스 등록만 지원합니다. DOCX/PDF로 저장하면 본문 추출까지 됩니다."
        else:
            warning = "이 파일 형식은 생성 소스로만 기록했습니다."
    except zipfile.BadZipFile:
        warning = "문서 압축 구조를 읽지 못해 소스 등록만 했습니다."
    except ValueError as exc:
        warning = str(exc)
    except Exception:
        logger.exception("problem_studio_extract_failed name=%s", name)
        warning = "본문 추출 중 오류가 발생해 소스 등록만 했습니다."

    if not text and not warning and name.lower().endswith(SUPPORTED_TEXT_SUFFIXES):
        warning = "본문 텍스트를 찾지 못했습니다. 스캔본이면 OCR 단계가 필요합니다."

    return SourceExtraction(
        name=name,
        kind=kind,
        size_label=_size_label(len(data)),
        extracted_text=text[:MAX_TEXT_CHARS],
        warning=warning,
    )


def extract_sources(source_files: Iterable[Any]) -> list[SourceExtraction]:
    return [extract_source(uploaded) for uploaded in source_files]


def source_extraction_to_payload(source: SourceExtraction) -> dict[str, Any]:
    return {
        "name": source.name,
        "kind": source.kind,
        "sizeLabel": source.size_label,
        "extracted_text": source.extracted_text,
        "warning": source.warning,
    }


def source_extraction_from_payload(raw: dict[str, Any]) -> SourceExtraction:
    return SourceExtraction(
        name=str(raw.get("name") or "source"),
        kind=str(raw.get("kind") or "기타"),
        size_label=str(raw.get("sizeLabel") or raw.get("size_label") or ""),
        extracted_text=str(raw.get("extracted_text") or raw.get("extractedText") or "")[:MAX_TEXT_CHARS],
        warning=str(raw["warning"]) if raw.get("warning") else None,
    )


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _question_text_from_payload(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    if isinstance(payload.get("text"), str):
        parts.append(str(payload["text"]))
    for q in _as_list(payload.get("questions")):
        if not isinstance(q, dict):
            continue
        q_parts = [
            str(q.get("prompt") or ""),
            str(q.get("choices") or ""),
            str(q.get("answer") or ""),
            str(q.get("explanation") or ""),
        ]
        joined = "\n".join(part for part in q_parts if part.strip())
        if joined.strip():
            parts.append(joined)
    return _normalize_space("\n\n".join(parts))


_QUESTION_SPLIT_RE = re.compile(
    r"\n(?=\s*(?:\d{1,3}\s*[.)]|문제\s*\d{1,3}|Q\s*\d{1,3})\s*)",
    re.IGNORECASE,
)
_LEADING_NUMBER_RE = re.compile(r"^\s*(?:\d{1,3}\s*[.)]|문제\s*\d{1,3}|Q\s*\d{1,3})\s*", re.IGNORECASE)
_CHOICE_RE = re.compile(r"^\s*(?:[①②③④⑤⑥⑦⑧⑨]|\([1-9]\)|[1-9]\))\s*")
_ANSWER_RE = re.compile(r"(?:정답|답)\s*[:：]?\s*([①②③④⑤⑥⑦⑧⑨1-9A-Ea-e]+)")
_EXPLANATION_RE = re.compile(r"(?:해설|풀이)\s*[:：]?\s*(.+)", re.DOTALL)


def _split_source_questions(text: str) -> list[str]:
    normalized = _normalize_space(text)
    if not normalized:
        return []
    blocks = [block.strip() for block in _QUESTION_SPLIT_RE.split(f"\n{normalized}") if block.strip()]
    if len(blocks) == 1 and len(blocks[0]) > 1800:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", blocks[0]) if p.strip()]
        blocks = paragraphs if len(paragraphs) > 1 else blocks
    return blocks[:MAX_OUTPUT_QUESTIONS]


def _extract_question_fields(block: str) -> dict[str, Any]:
    clean = _LEADING_NUMBER_RE.sub("", block).strip()
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    choices: list[str] = []
    body_lines: list[str] = []
    for line in lines:
        if _CHOICE_RE.match(line):
            choices.append(line)
        elif not _ANSWER_RE.search(line) and not line.startswith(("해설", "풀이")):
            body_lines.append(line)
    answer_match = _ANSWER_RE.search(clean)
    explanation_match = _EXPLANATION_RE.search(clean)
    return {
        "prompt": _normalize_space("\n".join(body_lines))[:3000],
        "choices": choices[:10],
        "answer": answer_match.group(1).strip() if answer_match else "",
        "explanation": _normalize_space(explanation_match.group(1))[:800] if explanation_match else "",
    }


def _mode_label(mode: str) -> str:
    return {
        "copy": "단순 복사/정리",
        "same-type": "유사 유형",
        "trap": "함정/오답 유도",
        "concept": "교과 개념형",
    }.get(mode, "단순 복사/정리")


def _fallback_explanation(mode: str, note_policy: str) -> str:
    policy = note_policy.strip() or "교과서 개념 중심으로 짧게 설명합니다."
    if mode == "trap":
        return f"{policy} 오답 유도 포인트는 비슷한 용어와 수치를 구분해 한 문장으로 확인합니다."
    if mode == "same-type":
        return f"{policy} 같은 개념과 풀이 순서를 유지했는지 검수합니다."
    if mode == "concept":
        return f"{policy} 핵심 정의와 적용 조건을 먼저 확인합니다."
    return f"{policy} 원본 구조를 정리한 초안이므로 정답은 검수 후 확정합니다."


def _fallback_questions(
    *,
    text: str,
    mode: str,
    count: int,
    note_policy: str,
) -> list[dict[str, Any]]:
    blocks = _split_source_questions(text)
    if not blocks:
        return []
    wanted_multiplier = 1 if mode == "copy" else max(1, count)
    output: list[dict[str, Any]] = []
    for block_index, block in enumerate(blocks, start=1):
        fields = _extract_question_fields(block)
        base_prompt = fields["prompt"] or block[:1200]
        for variant_index in range(1, wanted_multiplier + 1):
            suffix = ""
            if mode != "copy":
                suffix = f"\n\n[{_mode_label(mode)} 후보 {variant_index}] 같은 개념과 풀이 구조를 유지해 선생님이 변주를 확정합니다."
            output.append({
                "prompt": f"{base_prompt}{suffix}".strip(),
                "choices": fields["choices"],
                "answer": fields["answer"] or "검수 필요",
                "explanation": fields["explanation"] or _fallback_explanation(mode, note_policy),
                "source_index": block_index,
                "variant_index": variant_index,
            })
            if len(output) >= MAX_OUTPUT_QUESTIONS:
                return output
    return output


def _source_transfer_questions(text: str) -> list[dict[str, Any]]:
    blocks = _split_source_questions(text)
    if not blocks:
        return []
    output: list[dict[str, Any]] = []
    for block_index, block in enumerate(blocks, start=1):
        answer_match = _ANSWER_RE.search(block)
        explanation_match = _EXPLANATION_RE.search(block)
        output.append({
            "prompt": block[:4000],
            "choices": [],
            "answer": answer_match.group(1).strip() if answer_match else "원본 확인",
            "explanation": (
                _normalize_space(explanation_match.group(1))[:800]
                if explanation_match
                else "원본 자료를 한글 검수 파일로 그대로 옮긴 초안입니다. 선생님 검수 후 수정합니다."
            ),
            "source_index": block_index,
            "variant_index": 1,
        })
        if len(output) >= MAX_OUTPUT_QUESTIONS:
            break
    return output


def _normalize_mode(value: Any) -> str:
    mode = str(value or "copy").strip()
    return mode if mode in {"copy", "same-type", "trap", "concept"} else "copy"


def _normalize_count(value: Any) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = 3
    return max(1, min(MAX_VARIANT_COUNT, count))


def _try_ai_generation(*, text: str, mode: str, count: int, note_policy: str, subject: str) -> list[dict[str, Any]]:
    try:
        from academy.adapters.ai.problem.generator import generate_problem_package_from_text
        return generate_problem_package_from_text(
            source_text=text[:MAX_TEXT_CHARS],
            mode=mode,
            variant_count=count,
            note_policy=note_policy,
            subject=subject,
            max_questions=MAX_OUTPUT_QUESTIONS,
        )
    except Exception:
        logger.info("problem_studio_ai_generation_fallback", exc_info=True)
        return []


def build_problem_studio_package(
    *,
    payload: dict[str, Any],
    source_files: Iterable[Any],
) -> dict[str, Any]:
    return build_problem_studio_package_from_sources(
        payload=payload,
        sources=extract_sources(source_files),
    )


def build_problem_studio_package_from_sources(
    *,
    payload: dict[str, Any],
    sources: Iterable[SourceExtraction],
) -> dict[str, Any]:
    mode = _normalize_mode(payload.get("variant_mode"))
    count = _normalize_count(payload.get("variant_count"))
    note_policy = str(payload.get("note_policy") or "")
    subject = str(payload.get("subject") or "")
    use_ai = bool(payload.get("use_ai", True))
    transfer_only = bool(payload.get("transfer_only", False))

    sources = list(sources)
    combined_text = _normalize_space("\n\n".join(
        [src.extracted_text for src in sources if src.extracted_text.strip()]
        + [_question_text_from_payload(payload)]
    ))[:MAX_TEXT_CHARS]

    warnings = [src.warning for src in sources if src.warning]
    generation_engine = "rule_fallback"
    questions: list[dict[str, Any]] = []

    if transfer_only and combined_text:
        questions = _source_transfer_questions(combined_text)
        generation_engine = "source_transfer"

    if not questions and use_ai and combined_text:
        questions = _try_ai_generation(
            text=combined_text,
            mode=mode,
            count=count,
            note_policy=note_policy,
            subject=subject,
        )
        if questions:
            generation_engine = "ai"
        else:
            warnings.append("AI 생성이 불안정해 규칙 기반 초안으로 전환했습니다.")

    if not questions:
        questions = _fallback_questions(
            text=combined_text,
            mode=mode,
            count=count,
            note_policy=note_policy,
        )

    if not questions:
        questions = [{
            "prompt": "소스에서 본문 텍스트를 추출하지 못했습니다. 스캔본은 매치업/OCR 처리 후 다시 생성해 주세요.",
            "choices": [],
            "answer": "검수 필요",
            "explanation": _fallback_explanation(mode, note_policy),
            "source_index": 1,
            "variant_index": 1,
        }]
        warnings.append("본문 텍스트가 없어 검수 안내 문항을 만들었습니다.")

    return {
        "generation_engine": generation_engine,
        "mode": mode,
        "mode_label": "원본 이관" if transfer_only else _mode_label(mode),
        "variant_count": 1 if mode == "copy" else count,
        "questions": questions[:MAX_OUTPUT_QUESTIONS],
        "source_files": [
            {
                "name": src.name,
                "kind": src.kind,
                "sizeLabel": src.size_label,
                "extractedChars": len(src.extracted_text),
                "warning": src.warning,
            }
            for src in sources
        ],
        "warnings": [w for w in warnings if w],
        "source_text_chars": len(combined_text),
    }


def build_problem_studio_package_from_worker_payload(worker_payload: dict[str, Any]) -> dict[str, Any]:
    payload = worker_payload.get("problem_studio_payload")
    if not isinstance(payload, dict):
        payload = {}
    raw_sources = worker_payload.get("source_files")
    sources = [
        source_extraction_from_payload(source)
        for source in _as_list(raw_sources)
        if isinstance(source, dict)
    ]
    return build_problem_studio_package_from_sources(payload=payload, sources=sources)


def parse_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("payload JSON 형식이 올바르지 않습니다.") from exc
        if isinstance(parsed, dict):
            return parsed
    return {}
