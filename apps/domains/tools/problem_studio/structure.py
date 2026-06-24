from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


MAX_STRUCTURED_ITEMS = 80
MAX_ITEM_TEXT = 4500

_QUESTION_NUMBER_TOKEN = r"(?:[1-9]|[1-7]\d|80)"
_QUESTION_SPLIT_RE = re.compile(
    rf"\n(?=\s*(?:{_QUESTION_NUMBER_TOKEN}\s*(?:[.)]|\n)|문제\s*{_QUESTION_NUMBER_TOKEN}|Q\s*{_QUESTION_NUMBER_TOKEN})\s*)",
    re.IGNORECASE,
)
_LEADING_NUMBER_RE = re.compile(
    rf"^\s*(?:{_QUESTION_NUMBER_TOKEN}\s*(?:[.)]|\n)|문제\s*{_QUESTION_NUMBER_TOKEN}|Q\s*{_QUESTION_NUMBER_TOKEN})\s*",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(rf"^\s*(?P<number>{_QUESTION_NUMBER_TOKEN})\s*(?:[.)]|\n)")
_CHOICE_RE = re.compile(r"^\s*(?:[①②③④⑤⑥⑦⑧⑨]|\([1-9]\)|[1-9]\)|[A-Ea-e][.)])\s*")
_ANSWER_RE = re.compile(r"(?:정답|답)\s*[:：]?\s*(?P<answer>[①②③④⑤⑥⑦⑧⑨1-9A-Ea-e]+)")
_EXPLANATION_RE = re.compile(r"(?:해설|풀이)\s*[:：]?\s*(?P<explanation>.+)", re.DOTALL)


@dataclass(frozen=True)
class StructuredItem:
    number: int
    item_type: str
    source_name: str
    prompt: str
    choices: list[str] = field(default_factory=list)
    answer: str = ""
    explanation: str = ""
    confidence: float = 0.0
    review_flags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class TransferStructure:
    schema: str
    quality_level: str
    structured_item_count: int
    structured_problem_count: int
    concept_block_count: int
    ocr_candidate_count: int
    ocr_completed_unit_count: int
    ocr_pending_unit_count: int
    warning_count: int
    text_chars: int
    image_count: int
    page_count: int
    items: list[StructuredItem]
    ocr_candidates: list[dict[str, Any]]
    review_actions: list[str]

    def to_manifest(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["items"] = [asdict(item) for item in self.items]
        return payload


def normalize_space(text: str) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in str(text or "").replace("\r", "\n").split("\n")]
    compact: list[str] = []
    for line in lines:
        if line:
            compact.append(line)
        elif compact and compact[-1] != "":
            compact.append("")
    return "\n".join(compact).strip()


def split_source_blocks(text: str, *, max_blocks: int = MAX_STRUCTURED_ITEMS) -> list[str]:
    normalized = normalize_space(text)
    if not normalized:
        return []
    blocks = [block.strip() for block in _QUESTION_SPLIT_RE.split(f"\n{normalized}") if block.strip()]
    if len(blocks) == 1 and len(blocks[0]) > 1800:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", blocks[0]) if p.strip()]
        if len(paragraphs) > 1:
            blocks = paragraphs
    return blocks[:max_blocks]


def extract_problem_fields(block: str) -> dict[str, Any]:
    raw = normalize_space(block)
    number_match = _NUMBER_RE.match(raw)
    clean = _LEADING_NUMBER_RE.sub("", raw).strip()
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
    flags: list[str] = []
    if not choices:
        flags.append("보기 확인")
    if not answer_match:
        flags.append("정답 확인")
    if not explanation_match:
        flags.append("해설 확인")

    is_problem = bool(number_match or choices or answer_match)
    confidence = 0.55
    if number_match:
        confidence += 0.15
    if choices:
        confidence += 0.15
    if answer_match:
        confidence += 0.1
    if explanation_match:
        confidence += 0.05

    return {
        "number": int(number_match.group("number")) if number_match else 0,
        "item_type": "problem" if is_problem else "concept",
        "prompt": normalize_space("\n".join(body_lines))[:MAX_ITEM_TEXT] or clean[:MAX_ITEM_TEXT],
        "choices": choices[:10],
        "answer": answer_match.group("answer").strip() if answer_match else "",
        "explanation": normalize_space(explanation_match.group("explanation"))[:1200] if explanation_match else "",
        "confidence": min(confidence, 0.95),
        "review_flags": flags,
    }


def structure_text(*, source_name: str, text: str, start_number: int = 1) -> list[StructuredItem]:
    items: list[StructuredItem] = []
    for index, block in enumerate(split_source_blocks(text), start=start_number):
        fields = extract_problem_fields(block)
        number = int(fields["number"] or index)
        items.append(StructuredItem(
            number=number,
            item_type=str(fields["item_type"]),
            source_name=source_name,
            prompt=str(fields["prompt"]),
            choices=list(fields["choices"]),
            answer=str(fields["answer"]),
            explanation=str(fields["explanation"]),
            confidence=float(fields["confidence"]),
            review_flags=list(fields["review_flags"]),
        ))
    return items


def analyze_transfer_documents(documents: Iterable[Any], warnings: Iterable[str]) -> TransferStructure:
    docs = list(documents)
    warning_list = [str(w) for w in warnings if w]
    items: list[StructuredItem] = []
    ocr_candidates: list[dict[str, Any]] = []

    for doc in docs:
        plain_text = normalize_space(getattr(doc, "plain_text", "") or "")
        if plain_text:
            items.extend(structure_text(
                source_name=str(getattr(doc, "source_name", "") or getattr(doc, "filename", "") or "source"),
                text=plain_text,
                start_number=len(items) + 1,
            ))
        kind = str(getattr(doc, "kind", "") or "")
        pending_units = int(getattr(doc, "ocr_pending_units", 0) or 0)
        completed_units = int(getattr(doc, "ocr_completed_units", 0) or 0)
        if kind in {"PDF", "이미지"} and (pending_units > 0 or not plain_text):
            page_count = int(getattr(doc, "page_count", 0) or 0)
            image_count = int(getattr(doc, "image_count", 0) or 0)
            page_start = int(getattr(doc, "page_start", 0) or 0)
            page_end = int(getattr(doc, "page_end", 0) or 0)
            estimated_units = pending_units or page_count or image_count or 1
            reason = "텍스트 레이어가 없어 OCR 후 편집 가능한 본문을 만들 수 있습니다."
            recommended_action = "OCR 처리 후 01_자체양식_문제검수본.doc를 재생성하거나 수동 전사하세요."
            if completed_units and pending_units:
                reason = "자동 OCR이 일부 페이지만 처리되어 남은 페이지 확인이 필요합니다."
                recommended_action = "자동 OCR 텍스트를 원본 이미지와 대조하고, 남은 페이지는 후속 OCR 또는 수동 전사하세요."
            elif pending_units and plain_text:
                reason = "일부 페이지는 텍스트가 있지만 OCR 대기 페이지가 남아 있습니다."
                recommended_action = "텍스트가 있는 부분과 OCR 대기 쪽 범위를 분리해 검수하세요."
            ocr_candidates.append({
                "candidate_id": f"ocr-{len(ocr_candidates) + 1:03d}",
                "filename": str(getattr(doc, "filename", "") or ""),
                "source_name": str(getattr(doc, "source_name", "") or ""),
                "kind": kind,
                "page_count": page_count,
                "image_count": image_count,
                "page_start": page_start,
                "page_end": page_end,
                "estimated_units": estimated_units,
                "priority": "high" if kind == "이미지" or estimated_units <= 5 else "normal",
                "reason": reason,
                "recommended_action": recommended_action,
            })

    items = items[:MAX_STRUCTURED_ITEMS]
    structured_problem_count = sum(1 for item in items if item.item_type == "problem")
    concept_block_count = sum(1 for item in items if item.item_type == "concept")
    text_chars = sum(int(getattr(doc, "text_chars", 0) or 0) for doc in docs)
    image_count = sum(int(getattr(doc, "image_count", 0) or 0) for doc in docs)
    page_count = sum(int(getattr(doc, "page_count", 0) or 0) for doc in docs)
    ocr_completed_unit_count = sum(int(getattr(doc, "ocr_completed_units", 0) or 0) for doc in docs)
    ocr_pending_unit_count = sum(int(getattr(doc, "ocr_pending_units", 0) or 0) for doc in docs)

    if warning_list:
        quality_level = "needs_attention"
    elif ocr_candidates and not items:
        quality_level = "visual_only_ocr_required"
    elif ocr_candidates:
        quality_level = "mixed_review_ocr_recommended"
    elif items:
        quality_level = "structured_review_ready"
    else:
        quality_level = "manual_review_required"

    review_actions: list[str] = []
    if items:
        review_actions.append("01_자체양식_문제검수본.doc에서 문제 단위 분리 결과를 먼저 확인하세요.")
    if ocr_completed_unit_count:
        review_actions.append("자동 OCR 텍스트는 원본 이미지와 대조해 오인식, 수식, 선택지 누락을 표시하세요.")
    if ocr_candidates:
        review_actions.append("남은 스캔/이미지 전용 자료는 OCR 연결 후 텍스트 수정성을 높일 수 있습니다.")
    if warning_list:
        review_actions.append("경고가 있는 원본은 변환리포트와 원본 파일을 먼저 대조하세요.")
    review_actions.append("정답과 해설은 수업 배포 전 선생님이 직접 확정하세요.")

    return TransferStructure(
        schema="problem-studio-transfer-structure/v2",
        quality_level=quality_level,
        structured_item_count=len(items),
        structured_problem_count=structured_problem_count,
        concept_block_count=concept_block_count,
        ocr_candidate_count=len(ocr_candidates),
        ocr_completed_unit_count=ocr_completed_unit_count,
        ocr_pending_unit_count=ocr_pending_unit_count,
        warning_count=len(warning_list),
        text_chars=text_chars,
        image_count=image_count,
        page_count=page_count,
        items=items,
        ocr_candidates=ocr_candidates,
        review_actions=review_actions,
    )
