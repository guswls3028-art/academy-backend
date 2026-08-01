"""Build a resumable Beta answer/explanation PDF from one large workbook PDF.

This is the technical-prototype runner for validating the full 1,000-question
Problem Studio path before the same checkpoint contract is moved into the
tenant worker.  It never writes product data or consumes a tenant Beta run.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_NOTE_POLICY = (
    "통합과학 개념을 기준으로 정답 판별에 필요한 최소 근거만 간결하게 설명합니다. "
    "확실하지 않거나 판별에 불필요한 추가 사실은 쓰지 않습니다. 원본 모범답안이 "
    "있으면 반드시 그대로 사용합니다."
)
CHOICE_MAP = {str(index): value for index, value in enumerate("①②③④⑤⑥⑦⑧⑨", start=1)}
DEFAULT_BLANK_BEDROCK_MODEL = "us.amazon.nova-pro-v1:0"
DEFAULT_BLANK_BEDROCK_REGION = "us-east-1"
SOLVE_CONTRACT_VERSION = "problem-studio-explanation-v3"


def _boot_django() -> None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.test")
    import django

    django.setup()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _visual_extension(mime: str) -> str:
    return {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
    }.get(mime.lower(), ".png")


def _normalize_answer(value: str) -> str:
    compact = re.sub(r"\s+", "", str(value or "")).strip(".。()[]")
    leading_choice = re.match(r"^([①②③④⑤⑥⑦⑧⑨])", compact)
    if leading_choice:
        return leading_choice.group(1)
    leading_digit = re.match(r"^([1-9])(?:번|[.)：:]|\s|$)", str(value or "").strip())
    if leading_digit:
        return CHOICE_MAP[leading_digit.group(1)]
    if compact in CHOICE_MAP:
        return CHOICE_MAP[compact]
    match = re.fullmatch(r"(?:정답)?([①②③④⑤⑥⑦⑧⑨])", compact)
    return match.group(1) if match else compact


def _item_input_sha256(item: dict[str, Any]) -> str:
    payload = {
        key: item.get(key)
        for key in (
            "number",
            "prompt",
            "choices",
            "source_answer",
            "visual_file",
            "visual_mime",
            "visual_role",
        )
    }
    payload["solve_contract_version"] = SOLVE_CONTRACT_VERSION
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def extract_manifest(*, source: Path, work_dir: Path, expected_questions: int = 0) -> dict[str, Any]:
    from apps.domains.tools.problem_studio.structure import analyze_transfer_documents
    from apps.domains.tools.problem_studio.transfer_documents import (
        TransferOcrContext,
        _pdf_transfer_docs,
        _question_visual_map_by_source,
    )

    started = time.monotonic()
    source_bytes = source.read_bytes()
    documents = _pdf_transfer_docs(
        source.name,
        source_bytes,
        ocr_context=TransferOcrContext(enabled=False, max_units=0),
    )
    structure = analyze_transfer_documents(documents, [])
    problems = [item for item in structure.items if item.item_type == "problem"]
    numbers = [item.number for item in problems]
    unique_numbers = sorted(set(numbers))
    if not unique_numbers:
        raise RuntimeError("구조화된 문항이 없습니다.")
    expected_sequence = list(range(unique_numbers[0], unique_numbers[-1] + 1))
    if numbers != expected_sequence:
        missing = sorted(set(expected_sequence) - set(numbers))
        duplicates = sorted(number for number in set(numbers) if numbers.count(number) > 1)
        raise RuntimeError(
            f"문항 번호가 연속·고유하지 않습니다. missing={missing[:20]} duplicates={duplicates[:20]}"
        )
    if expected_questions and len(problems) != expected_questions:
        raise RuntimeError(
            f"예상 문항 수 불일치: expected={expected_questions} actual={len(problems)}"
        )

    question_crops: dict[tuple[str, int], Any] = {}
    for document in documents:
        for visual in document.question_visuals:
            if visual.role == "question_crop":
                question_crops.setdefault((visual.source_name, visual.question_number), visual)
    if len(question_crops) != len(problems):
        raise RuntimeError(
            f"문항 crop 수 불일치: problems={len(problems)} crops={len(question_crops)}"
        )

    model_visuals = _question_visual_map_by_source(documents)
    visual_dir = work_dir / "visuals"
    visual_dir.mkdir(parents=True, exist_ok=True)
    manifest_items: list[dict[str, Any]] = []
    for item in problems:
        key = (item.source_name, item.number)
        crop = question_crops[key]
        visual = model_visuals.get(key)
        visual_role = "focused_fragment" if visual else ""
        if visual is None and set(crop.semantic_flags) & {"visual_context", "shared_context"}:
            visual = {
                "data": crop.data,
                "mime": crop.mime,
            }
            visual_role = "question_crop"
        visual_file = ""
        if visual:
            extension = _visual_extension(str(visual.get("mime") or ""))
            visual_path = visual_dir / f"question-{item.number:04d}{extension}"
            visual_path.write_bytes(visual["data"])
            visual_file = visual_path.relative_to(work_dir).as_posix()
        manifest_item = {
            "number": item.number,
            "page_number": crop.page_number,
            "prompt": item.prompt,
            "choices": item.choices,
            "source_answer": item.answer,
            "source_answer_check": item.answer_check,
            "visual_file": visual_file,
            "visual_mime": str((visual or {}).get("mime") or ""),
            "visual_role": visual_role,
        }
        manifest_item["input_sha256"] = _item_input_sha256(manifest_item)
        manifest_items.append(manifest_item)

    manifest = {
        "schema": "problem-studio-pdf-prototype/v1",
        "beta": True,
        "source": {
            "name": source.name,
            "path": str(source.resolve()),
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "size_bytes": len(source_bytes),
            "page_count": sum(document.page_count for document in documents),
        },
        "metrics": {
            "question_count": len(problems),
            "question_crop_count": len(question_crops),
            "focused_visual_count": len(model_visuals),
            "model_visual_count": sum(bool(item["visual_file"]) for item in manifest_items),
            "source_answer_count": sum(bool(item.answer) for item in problems),
            "missing_source_answer_count": sum(not bool(item.answer) for item in problems),
            "extraction_seconds": round(time.monotonic() - started, 2),
        },
        "items": manifest_items,
    }
    _atomic_json(work_dir / "manifest.json", manifest)
    return manifest


def _question_payload(item: dict[str, Any], *, work_dir: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prompt": item["prompt"],
        "choices": item.get("choices") or [],
        "answer": item.get("source_answer") or "",
        "explanation": "",
    }
    visual_file = str(item.get("visual_file") or "")
    if visual_file:
        visual_path = work_dir / visual_file
        payload["visual"] = {
            "data": visual_path.read_bytes(),
            "mime": item.get("visual_mime") or "image/png",
        }
    return payload


def _save_solutions(path: Path, state: dict[str, Any]) -> None:
    state["updated_at_epoch"] = int(time.time())
    _atomic_json(path, state)


def _pending_batches(values: list[Any], batch_size: int) -> Iterable[list[Any]]:
    for start in range(0, len(values), batch_size):
        yield values[start:start + batch_size]


def _is_korean_explanation(value: str) -> bool:
    source = str(value or "")
    return len(re.findall(r"[가-힣]", source)) >= 10 and not re.search(
        r"[\u4e00-\u9fff]",
        source,
    )


def _is_objective_item(item: dict[str, Any]) -> bool:
    choices = [str(value or "").strip() for value in item.get("choices") or []]
    if len(choices) < 2 or not all(re.match(r"^[①②③④⑤⑥⑦⑧⑨]", value) for value in choices):
        return False
    combined = "\n".join([str(item.get("prompt") or ""), *choices])
    return not re.search(r"(?:서술하시오|설명하시오|작성하시오|구하시오|쓰시오)", combined)


def _objective_result_is_consistent(
    item: dict[str, Any],
    result: dict[str, Any],
) -> bool:
    if not _is_objective_item(item):
        return True
    choices = [str(value or "").strip() for value in item.get("choices") or []]
    answer = _normalize_answer(str(result.get("answer") or ""))
    symbols = "①②③④⑤⑥⑦⑧⑨"
    if answer not in symbols[:len(choices)]:
        return False
    selected_choice = choices[symbols.index(answer)]
    returned_choice = re.sub(r"\s+", "", str(result.get("selected_choice_text") or ""))
    if returned_choice != re.sub(r"\s+", "", selected_choice):
        return False
    selected_truths = set(re.findall(r"[ㄱㄴㄷㄹㅁ]", selected_choice))
    returned_truths = {
        str(value).strip()
        for value in (result.get("true_statements") or [])
        if str(value).strip()
    }
    return not selected_truths or selected_truths == returned_truths


def _reconcile_objective_answer(
    item: dict[str, Any],
    result: dict[str, Any],
) -> dict[str, Any]:
    if not _is_objective_item(item):
        return result
    returned_truths = {
        str(value).strip()
        for value in (result.get("true_statements") or [])
        if str(value).strip() in {"ㄱ", "ㄴ", "ㄷ", "ㄹ", "ㅁ"}
    }
    if not returned_truths:
        return result
    choices = [str(value or "").strip() for value in item.get("choices") or []]
    matching_indexes = [
        index
        for index, choice in enumerate(choices)
        if set(re.findall(r"[ㄱㄴㄷㄹㅁ]", choice)) == returned_truths
    ]
    if len(matching_indexes) != 1:
        return result
    selected_index = matching_indexes[0]
    reconciled = dict(result)
    reconciled["answer"] = "①②③④⑤⑥⑦⑧⑨"[selected_index]
    reconciled["selected_choice_text"] = choices[selected_index]
    return reconciled


def solve_manifest(
    *,
    manifest: dict[str, Any],
    work_dir: Path,
    batch_size: int,
    max_retries: int,
    limit: int = 0,
    blank_bedrock_model: str = DEFAULT_BLANK_BEDROCK_MODEL,
    blank_bedrock_region: str = DEFAULT_BLANK_BEDROCK_REGION,
) -> dict[str, Any]:
    from academy.adapters.ai.config import AIConfig
    from academy.adapters.ai.problem.generator import generate_transcribed_explanations

    solution_path = work_dir / "solutions.json"
    state = _load_json(solution_path, {"schema": "problem-studio-solutions/v1", "items": {}})
    solved = state.setdefault("items", {})
    pending = [
        item
        for item in manifest["items"]
        if (
            not str((solved.get(str(item["number"])) or {}).get("explanation") or "").strip()
            or str((solved.get(str(item["number"])) or {}).get("input_sha256") or "")
            != str(item.get("input_sha256") or "")
        )
    ]
    pending.sort(key=lambda item: not bool(item.get("source_answer")))
    if limit:
        pending = pending[:limit]
    cfg = AIConfig.load()
    state["provider"] = "openai" if cfg.OPENAI_API_KEY else "bedrock"
    state["model"] = (
        cfg.PROBLEM_STUDIO_EXPLANATION_MODEL
        if cfg.OPENAI_API_KEY
        else cfg.PROBLEM_GEN_BEDROCK_MODEL
    )

    total_pending = len(pending)
    completed = 0
    failures: list[int] = []
    for batch in _pending_batches(pending, batch_size):
        remaining = list(batch)
        for attempt in range(1, max_retries + 1):
            if not remaining:
                break
            selected_bedrock_model = (
                blank_bedrock_model
                if not cfg.OPENAI_API_KEY
                and any(not item.get("source_answer") for item in remaining)
                else ""
            )
            selected_bedrock_region = (
                blank_bedrock_region if selected_bedrock_model else ""
            )
            try:
                generated = generate_transcribed_explanations(
                    questions=[_question_payload(item, work_dir=work_dir) for item in remaining],
                    subject="통합과학",
                    note_policy=DEFAULT_NOTE_POLICY,
                    model=cfg.PROBLEM_STUDIO_EXPLANATION_MODEL,
                    bedrock_model=selected_bedrock_model,
                    bedrock_region=selected_bedrock_region,
                )
            except Exception as exc:
                print(json.dumps({
                    "event": "solve_retry",
                    "attempt": attempt,
                    "numbers": [item["number"] for item in remaining],
                    "error": f"{type(exc).__name__}: {str(exc)[:240]}",
                }, ensure_ascii=False), flush=True)
                if attempt < max_retries:
                    time.sleep(min(10, 2 ** attempt))
                continue

            by_local_index = {
                int(result["index"]): result
                for result in generated
                if str(result.get("index") or "").isdigit()
            }
            next_remaining: list[dict[str, Any]] = []
            for local_index, item in enumerate(remaining, start=1):
                result = by_local_index.get(local_index)
                if result and not item.get("source_answer"):
                    result = _reconcile_objective_answer(item, result)
                if (
                    not result
                    or not _is_korean_explanation(str(result.get("explanation") or ""))
                    or (
                        not item.get("source_answer")
                        and not _objective_result_is_consistent(item, result)
                    )
                ):
                    next_remaining.append(item)
                    continue
                number = int(item["number"])
                source_answer = str(item.get("source_answer") or "").strip()
                generated_answer = str(result.get("answer") or "검수 필요").strip()
                if not source_answer and _is_objective_item(item):
                    generated_answer = _normalize_answer(generated_answer)
                solved[str(number)] = {
                    "number": number,
                    "input_sha256": str(item.get("input_sha256") or ""),
                    "answer": source_answer or generated_answer,
                    "model_answer": str(result.get("answer") or "").strip(),
                    "explanation": str(result.get("explanation") or "").strip(),
                    "answer_check": str(result.get("answer_check") or "").strip(),
                    "confidence": str(result.get("confidence") or "low").strip(),
                    "answer_source": "source_reference" if source_answer else "ai_generated",
                    "model": (
                        cfg.PROBLEM_STUDIO_EXPLANATION_MODEL
                        if cfg.OPENAI_API_KEY
                        else selected_bedrock_model or cfg.PROBLEM_GEN_BEDROCK_MODEL
                    ),
                }
            remaining = next_remaining
            _save_solutions(solution_path, state)
            if remaining and attempt < max_retries:
                time.sleep(min(10, 2 ** attempt))
        failures.extend(int(item["number"]) for item in remaining)
        for item in remaining:
            number = int(item["number"])
            source_answer = str(item.get("source_answer") or "").strip()
            solved[str(number)] = {
                "number": number,
                "input_sha256": str(item.get("input_sha256") or ""),
                "answer": source_answer or "검수 필요",
                "model_answer": "",
                "explanation": (
                    "자동 풀이가 한국어·선택지 일치 검증을 반복해서 통과하지 못했습니다. "
                    "원문 도식과 선택지를 선생님이 직접 확인해 주세요."
                ),
                "answer_check": "자동 검증 실패로 정답을 확정하지 않음",
                "confidence": "low",
                "answer_source": "source_reference" if source_answer else "ai_generated",
                "model": (
                    cfg.PROBLEM_STUDIO_EXPLANATION_MODEL
                    if cfg.OPENAI_API_KEY
                    else selected_bedrock_model or cfg.PROBLEM_GEN_BEDROCK_MODEL
                ),
                "verification_status": "solve_validation_failed",
            }
        if remaining:
            _save_solutions(solution_path, state)
        completed += len(batch) - len(remaining)
        print(json.dumps({
            "event": "solve_progress",
            "completed": completed,
            "requested": total_pending,
            "total_solved": len(solved),
            "failed_numbers": [item["number"] for item in remaining],
        }, ensure_ascii=False), flush=True)

    state["solve_failures"] = sorted(set(failures))
    _save_solutions(solution_path, state)
    return state


def verify_solutions(
    *,
    manifest: dict[str, Any],
    work_dir: Path,
    batch_size: int,
    max_retries: int,
    limit: int = 0,
    force: bool = False,
    mismatches_only: bool = False,
) -> dict[str, Any]:
    from academy.adapters.ai.config import AIConfig
    from academy.adapters.ai.problem.generator import generate_transcribed_explanations

    solution_path = work_dir / "solutions.json"
    state = _load_json(solution_path, {"schema": "problem-studio-solutions/v1", "items": {}})
    solved = state.setdefault("items", {})
    manifest_by_number = {int(item["number"]): item for item in manifest["items"]}
    pending: list[dict[str, Any]] = []
    for number, item in manifest_by_number.items():
        solution = solved.get(str(number)) or {}
        status = str(solution.get("verification_status") or "")
        if (
            not solution.get("explanation")
            or str(solution.get("input_sha256") or "")
            != str(item.get("input_sha256") or "")
        ):
            continue
        if item.get("source_answer") and not item.get("choices"):
            solution["verification_status"] = "source_reference_written"
            solved[str(number)] = solution
            continue
        if mismatches_only:
            should_verify = "mismatch" in status
        else:
            should_verify = force or not status
        if should_verify:
            pending.append(item)
    if limit:
        pending = pending[:limit]

    cfg = AIConfig.load()
    completed = 0
    failures: list[int] = []
    for batch in _pending_batches(pending, batch_size):
        remaining = list(batch)
        for attempt in range(1, max_retries + 1):
            if not remaining:
                break
            independent_payloads = []
            for item in remaining:
                payload = _question_payload(item, work_dir=work_dir)
                payload["answer"] = ""
                independent_payloads.append(payload)
            try:
                generated = generate_transcribed_explanations(
                    questions=independent_payloads,
                    subject="통합과학",
                    note_policy=(
                        "앞선 풀이를 보지 않은 독립 검산입니다. 문제를 처음부터 다시 풀고 "
                        "정답 근거를 짧게 제시하세요."
                    ),
                    model=cfg.PROBLEM_STUDIO_EXPLANATION_MODEL,
                )
            except Exception as exc:
                print(json.dumps({
                    "event": "verify_retry",
                    "attempt": attempt,
                    "numbers": [item["number"] for item in remaining],
                    "error": f"{type(exc).__name__}: {str(exc)[:240]}",
                }, ensure_ascii=False), flush=True)
                if attempt < max_retries:
                    time.sleep(min(10, 2 ** attempt))
                continue

            by_local_index = {
                int(result["index"]): result
                for result in generated
                if str(result.get("index") or "").isdigit()
            }
            next_remaining: list[dict[str, Any]] = []
            for local_index, item in enumerate(remaining, start=1):
                result = by_local_index.get(local_index)
                if not result or not str(result.get("answer") or "").strip():
                    next_remaining.append(item)
                    continue
                number = int(item["number"])
                solution = solved[str(number)]
                first_answer = _normalize_answer(str(solution.get("answer") or ""))
                second_answer = _normalize_answer(str(result.get("answer") or ""))
                source_reference = bool(item.get("source_answer"))
                answers_match = bool(first_answer and first_answer == second_answer)
                solution.update({
                    "verification_answer": str(result.get("answer") or "").strip(),
                    "verification_explanation": str(result.get("explanation") or "").strip(),
                    "verification_reason": str(result.get("answer_check") or "").strip(),
                    "verification_status": (
                        "source_reference_ai_match"
                        if source_reference and answers_match
                        else "source_reference_ai_mismatch"
                        if source_reference
                        else "ai_match"
                        if answers_match
                        else "ai_mismatch"
                    ),
                    "verification_model": (
                        cfg.PROBLEM_STUDIO_EXPLANATION_MODEL
                        if cfg.OPENAI_API_KEY
                        else cfg.PROBLEM_GEN_BEDROCK_MODEL
                    ),
                })
                if source_reference and answers_match:
                    solution["explanation"] = solution["verification_explanation"]
                    solution["answer_check"] = solution["verification_reason"]
            remaining = next_remaining
            _save_solutions(solution_path, state)
            if remaining and attempt < max_retries:
                time.sleep(min(10, 2 ** attempt))
        failures.extend(int(item["number"]) for item in remaining)
        completed += len(batch) - len(remaining)
        print(json.dumps({
            "event": "verify_progress",
            "completed": completed,
            "requested": len(pending),
            "failed_numbers": [item["number"] for item in remaining],
        }, ensure_ascii=False), flush=True)

    state["verify_failures"] = sorted(set(failures))
    _save_solutions(solution_path, state)
    return state


def _verification_label(solution: dict[str, Any]) -> tuple[str, str]:
    status = str(solution.get("verification_status") or "")
    if (
        not status
        and solution.get("answer_source") == "source_reference"
        and str(solution.get("confidence") or "").lower() == "low"
    ):
        return "검수 필요 · 원본답 해설 불충분", "#b91c1c"
    if not status and solution.get("answer_source") == "source_reference":
        return "원본 모범답안 · AI 해설 검수 필요", "#0f766e"
    if status == "manual_source_review":
        return "원본 모범답안 · 직접 검산", "#047857"
    if status == "source_reference_ai_match":
        return "원본 모범답안 · 독립 풀이 일치", "#0f766e"
    if status == "source_reference_written":
        return "원본 서술형 모범답안", "#0f766e"
    if status == "source_reference_ai_mismatch":
        return "검수 필요 · 원본답과 AI 풀이 불일치", "#b91c1c"
    if status == "ai_match":
        return "AI 독립 풀이 2회 일치", "#2563eb"
    if status == "ai_mismatch":
        return "검수 필요 · AI 정답 불일치", "#b91c1c"
    if status == "solve_validation_failed":
        return "검수 필요 · 자동 풀이 검증 실패", "#b91c1c"
    return "검수 필요 · AI 1차 풀이", "#b45309"


def _build_appendix_pdf(
    *,
    target: Path,
    manifest: dict[str, Any],
    solutions: dict[str, Any],
) -> None:
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        BaseDocTemplate,
        CondPageBreak,
        Frame,
        PageTemplate,
        Paragraph,
        Spacer,
    )

    regular_font = Path(r"C:\Windows\Fonts\malgun.ttf")
    bold_font = Path(r"C:\Windows\Fonts\malgunbd.ttf")
    if not regular_font.exists() or not bold_font.exists():
        raise RuntimeError("맑은 고딕 글꼴을 찾을 수 없습니다.")
    pdfmetrics.registerFont(TTFont("Malgun", str(regular_font)))
    pdfmetrics.registerFont(TTFont("MalgunBold", str(bold_font)))

    width, height = A4
    left = right = 17 * mm
    top = 20 * mm
    bottom = 17 * mm
    frame = Frame(left, bottom, width - left - right, height - top - bottom, id="body")

    def decorate(canvas, doc) -> None:
        canvas.saveState()
        canvas.setFont("Malgun", 7.5)
        canvas.setFillColorRGB(0.35, 0.38, 0.43)
        canvas.drawString(left, height - 11 * mm, "통합과학 정답·해설 Beta · 선생님 최종 검수 필수")
        canvas.drawRightString(width - right, 9 * mm, f"해설 {doc.page}쪽")
        canvas.restoreState()

    document = BaseDocTemplate(
        str(target),
        pagesize=A4,
        leftMargin=left,
        rightMargin=right,
        topMargin=top,
        bottomMargin=bottom,
        title="통합과학 정답·해설 Beta",
        author="Academy Problem Studio Beta",
    )
    document.addPageTemplates([PageTemplate(id="solutions", frames=[frame], onPage=decorate)])
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "KoreanTitle",
        parent=styles["Title"],
        fontName="MalgunBold",
        fontSize=18,
        leading=25,
        alignment=TA_CENTER,
        textColor="#111827",
        spaceAfter=8 * mm,
    )
    intro_style = ParagraphStyle(
        "KoreanIntro",
        fontName="Malgun",
        fontSize=9.5,
        leading=15,
        textColor="#374151",
        spaceAfter=5 * mm,
    )
    heading_style = ParagraphStyle(
        "SolutionHeading",
        fontName="MalgunBold",
        fontSize=10.2,
        leading=14,
        textColor="#111827",
        spaceBefore=2 * mm,
        spaceAfter=1.2 * mm,
    )
    body_style = ParagraphStyle(
        "SolutionBody",
        fontName="Malgun",
        fontSize=8.5,
        leading=13,
        textColor="#1f2937",
        spaceAfter=1.2 * mm,
    )
    check_style = ParagraphStyle(
        "SolutionCheck",
        fontName="Malgun",
        fontSize=7.5,
        leading=11,
        textColor="#6b7280",
        leftIndent=3 * mm,
        borderColor="#d1d5db",
        borderWidth=0.5,
        borderPadding=2 * mm,
        spaceAfter=2.5 * mm,
    )

    story = [
        Spacer(1, 10 * mm),
        Paragraph("통합과학 정답·해설 <font color='#2563eb'>Beta</font>", title_style),
        Paragraph(
            f"원본 문제집 {manifest['source']['page_count']}쪽 뒤에 붙는 해설 부록입니다. "
            "원본에 적힌 모범답안은 그대로 "
            "보존했고, 빈 정답은 경제형 AI로 1차 풀이했습니다. 문항마다 답의 출처와 검산 "
            "상태를 구분해 표시했습니다. "
            "AI 생성 해설과 ‘검수 필요’ 문항은 수업 배포 전에 반드시 선생님이 확인하세요.",
            intro_style,
        ),
        Spacer(1, 5 * mm),
    ]
    solved = solutions.get("items") or {}
    for item in manifest["items"]:
        number = int(item["number"])
        solution = solved.get(str(number)) or {}
        answer = html.escape(str(solution.get("answer") or "검수 필요"))
        label, color = _verification_label(solution)
        explanation = html.escape(str(solution.get("explanation") or "해설 생성 실패 · 검수 필요")).replace("\n", "<br/>")
        check = html.escape(str(solution.get("answer_check") or "근거 기록 없음")).replace("\n", "<br/>")
        story.extend([
            CondPageBreak(34 * mm),
            Paragraph(
                f"{number}번 &nbsp; <font color='#111827'>정답 {answer}</font> "
                f"<font name='Malgun' size='7.5' color='{color}'>[{html.escape(label)}]</font>",
                heading_style,
            ),
            Paragraph(explanation, body_style),
            Paragraph(f"검산 메모: {check}", check_style),
        ])
    document.build(story)


def build_output_pdf(
    *,
    source: Path,
    output: Path,
    work_dir: Path,
    manifest: dict[str, Any],
    allow_incomplete: bool,
) -> dict[str, Any]:
    import fitz

    solution_path = work_dir / "solutions.json"
    solutions = _load_json(solution_path, {"items": {}})
    solved = solutions.get("items") or {}
    missing = [
        int(item["number"])
        for item in manifest["items"]
        if (
            not str((solved.get(str(item["number"])) or {}).get("explanation") or "").strip()
            or str((solved.get(str(item["number"])) or {}).get("input_sha256") or "")
            != str(item.get("input_sha256") or "")
        )
    ]
    if missing and not allow_incomplete:
        raise RuntimeError(f"해설 미완료 문항이 남았습니다: {missing[:30]} (총 {len(missing)}개)")

    appendix = work_dir / "solutions-appendix.pdf"
    _build_appendix_pdf(target=appendix, manifest=manifest, solutions=solutions)
    output.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open(str(source)) as source_reader, fitz.open(str(appendix)) as appendix_reader:
        source_pages = source_reader.page_count
        appendix_pages = appendix_reader.page_count
        with fitz.open() as merged:
            merged.insert_pdf(source_reader)
            merged.insert_pdf(appendix_reader)
            merged.set_metadata({
                "title": f"{source.stem} 정답·해설 Beta",
                "author": "Academy Problem Studio Beta",
                "subject": "AI 생성 해설 · 선생님 최종 검수 필수",
            })
            merged.save(str(output), garbage=4, deflate=True)

    verification_counts: dict[str, int] = {}
    answer_source_counts: dict[str, int] = {}
    model_counts: dict[str, int] = {}
    for solution in solved.values():
        status = str(solution.get("verification_status") or "not_verified")
        verification_counts[status] = verification_counts.get(status, 0) + 1
        answer_source = str(solution.get("answer_source") or "unknown")
        answer_source_counts[answer_source] = answer_source_counts.get(answer_source, 0) + 1
        used_model = str(solution.get("model") or "unknown")
        model_counts[used_model] = model_counts.get(used_model, 0) + 1
    report = {
        "schema": "problem-studio-pdf-quality-report/v1",
        "beta": True,
        "source_sha256": _sha256(source),
        "output_sha256": _sha256(output),
        "source_pages": source_pages,
        "appendix_pages": appendix_pages,
        "output_pages": source_pages + appendix_pages,
        "question_count": len(manifest["items"]),
        "solution_count": len(manifest["items"]) - len(missing),
        "missing_solution_numbers": missing,
        "verification_counts": verification_counts,
        "answer_source_counts": answer_source_counts,
        "model_counts": model_counts,
        "output_path": str(output.resolve()),
    }
    _atomic_json(work_dir / "quality-report.json", report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("extract", "solve", "verify", "build", "all"))
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-questions", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--allow-incomplete", action="store_true")
    parser.add_argument("--force-verify", action="store_true")
    parser.add_argument("--mismatches-only", action="store_true")
    parser.add_argument(
        "--blank-bedrock-model",
        default=DEFAULT_BLANK_BEDROCK_MODEL,
        help="Bedrock model used only when the source appendix has no answer.",
    )
    parser.add_argument(
        "--blank-bedrock-region",
        default=DEFAULT_BLANK_BEDROCK_REGION,
        help="Bedrock region for the unanswered-source model.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source.resolve()
    work_dir = args.work_dir.resolve()
    output = (
        args.output.resolve()
        if args.output
        else work_dir / f"{source.stem}_정답해설_Beta.pdf"
    )
    if not source.is_file():
        raise FileNotFoundError(source)
    if not 1 <= args.batch_size <= 10:
        raise ValueError("--batch-size는 1~10이어야 합니다.")
    work_dir.mkdir(parents=True, exist_ok=True)
    _boot_django()

    manifest_path = work_dir / "manifest.json"
    if args.command in {"extract", "all"} or not manifest_path.exists():
        manifest = extract_manifest(
            source=source,
            work_dir=work_dir,
            expected_questions=args.expected_questions,
        )
        print(json.dumps({"event": "extracted", **manifest["metrics"]}, ensure_ascii=False), flush=True)
        if args.command == "extract":
            return 0
    else:
        manifest = _load_json(manifest_path, {})
        if manifest.get("source", {}).get("sha256") != _sha256(source):
            raise RuntimeError("원본 PDF가 manifest 생성 뒤 변경되었습니다. extract를 다시 실행하세요.")

    if args.command in {"solve", "all"}:
        state = solve_manifest(
            manifest=manifest,
            work_dir=work_dir,
            batch_size=args.batch_size,
            max_retries=args.max_retries,
            limit=args.limit,
            blank_bedrock_model=args.blank_bedrock_model,
            blank_bedrock_region=args.blank_bedrock_region,
        )
        if state.get("solve_failures"):
            return 2
        if args.command == "solve":
            return 0

    if args.command in {"verify", "all"}:
        state = verify_solutions(
            manifest=manifest,
            work_dir=work_dir,
            batch_size=args.batch_size,
            max_retries=args.max_retries,
            limit=args.limit,
            force=args.force_verify,
            mismatches_only=args.mismatches_only,
        )
        if state.get("verify_failures"):
            return 3
        if args.command == "verify":
            return 0

    if args.command in {"build", "all"}:
        report = build_output_pdf(
            source=source,
            output=output,
            work_dir=work_dir,
            manifest=manifest,
            allow_incomplete=args.allow_incomplete,
        )
        print(json.dumps({"event": "built", **report}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
