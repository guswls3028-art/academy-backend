"""Conversion checkpoints and review proposals, executed only by the AI worker."""
from __future__ import annotations

import copy
import hashlib
import io
import logging
import math
from dataclasses import asdict
from pathlib import Path

from django.db import transaction

from apps.domains.tools.problem_studio.models import ProblemStudioConversion
from apps.shared.contracts.ai_result import AIResult

logger = logging.getLogger(__name__)


class SupersededConversion(Exception):
    pass


def _query(job):
    payload = job.payload or {}
    if not job.tenant_id or str(payload.get("tenant_id")) != str(job.tenant_id) or not payload.get("request_user_id"):
        raise ValueError("Conversion ownership is missing")
    return ProblemStudioConversion.objects.filter(
        id=payload.get("conversion_id"), tenant_id=job.tenant_id,
        owner_id=payload["request_user_id"], operation__token=payload.get("operation_token"),
    )


def _save(job, **values):
    with transaction.atomic():
        row = _query(job).select_for_update().first()
        if row is None or row.version != row.operation.get("version"):
            raise SupersededConversion()
        for key, value in values.items():
            setattr(row, key, value)
        row.save(update_fields=[*values, "updated_at"])
    return row


def _put(row, name, data, mime, **metadata):
    from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage
    digest = hashlib.sha256(data).hexdigest()
    key = f"tenants/{row.tenant_id}/tools/problem-studio/conversions/{row.id}/{digest[:20]}/{name}"
    upload_fileobj_to_r2_storage(fileobj=io.BytesIO(data), key=key, content_type=mime)
    return {"key": key, "filename": name, "mime": mime, "size": len(data), "sha256": digest, **metadata}


def _read(row, asset):
    from academy.adapters.ai.storage.downloader import download_r2_key_to_tmp, cleanup_tmp_for_path
    prefix = f"tenants/{row.tenant_id}/tools/problem-studio/conversions/{row.id}/"
    if not asset or not str(asset.get("key", "")).startswith(prefix):
        raise ValueError("Invalid conversion asset")
    path = download_r2_key_to_tmp(r2_key=asset["key"], job_id=f"conversion-{row.id}")
    try:
        data = Path(path).read_bytes()
        if len(data) != asset["size"] or hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise ValueError("Conversion asset integrity mismatch")
        return data
    finally:
        cleanup_tmp_for_path(path)


def _extract(job, row):
    from academy.adapters.ai.config import AIConfig
    from academy.adapters.ai.problem.transcriber import transcribe_problem_image
    from academy.adapters.ai.storage.downloader import download_r2_key_to_tmp, cleanup_tmp_for_path
    from apps.support.tools.ai_dependencies import consume_tools_ai_quota
    from apps.domains.tools.problem_studio.async_transfer import source_files_from_archive
    from apps.domains.tools.problem_studio.ocr import OcrResult
    from apps.domains.tools.problem_studio.structure import analyze_transfer_documents
    from apps.domains.tools.problem_studio.transfer_documents import TransferOcrContext, collect_transfer_documents

    cfg = AIConfig.load()
    checkpoints = copy.deepcopy(row.checkpoints)

    def transcribe(data, mime):
        digest = hashlib.sha256(data).hexdigest()
        if digest in checkpoints:
            return OcrResult(text=checkpoints[digest], status="extracted", engine="checkpoint")
        consume_tools_ai_quota(kind="problem_studio_transcription")
        text = transcribe_problem_image(
            data, mime=mime, api_key=cfg.OPENAI_API_KEY or "", model=cfg.PROBLEM_TRANSCRIPTION_MODEL,
            bedrock_model=cfg.PROBLEM_TRANSCRIPTION_BEDROCK_MODEL, bedrock_region=cfg.BEDROCK_REGION,
        )
        if not text:
            raise ValueError("원본의 글자를 읽지 못했습니다. 더 선명한 파일을 사용해 주세요.")
        checkpoints[digest] = text
        _save(job, checkpoints=checkpoints, stage="transcribe")
        return OcrResult(text=text, status="extracted", engine="ai")

    prefix = f"tenants/{row.tenant_id}/tools/problem-studio/conversions/{row.id}/"
    if not row.source_archive_key.startswith(prefix):
        raise ValueError("Invalid conversion source")
    path = download_r2_key_to_tmp(r2_key=row.source_archive_key, job_id=f"conversion-{row.id}")
    try:
        with source_files_from_archive(path) as files:
            collection = collect_transfer_documents(source_files=files, ocr_context=TransferOcrContext(max_units=250, extractor=transcribe))
    finally:
        cleanup_tmp_for_path(path)
    structure = analyze_transfer_documents(collection.documents, collection.warnings)
    if structure.ocr_pending_unit_count or structure.structure_limit_reached:
        raise ValueError("일부 페이지를 읽지 못했습니다. 파일을 나누거나 선명한 원본으로 다시 변환해 주세요.")
    items = [item for item in structure.items if item.item_type == "problem"]
    if not items:
        raise ValueError("원본에서 문항을 찾지 못했습니다. 문항 번호와 글자가 보이는 시험지를 올려 주세요.")
    pages, page_indexes, visuals, crops = [], {}, {}, {}
    for document in collection.documents:
        for page in document.source_pages:
            key = (page.source_name, page.page_number)
            if key not in page_indexes:
                page_indexes[key] = len(pages)
                pages.append(_put(row, f"page-{len(pages) + 1}.png", page.data, page.mime,
                                  source_name=page.source_name, page_number=page.page_number,
                                  width=page.width_px, height=page.height_px))
        for visual in document.question_visuals:
            key = (visual.source_name, visual.question_number)
            if visual.role == "question_crop":
                crops.setdefault(key, visual)
            elif visual.role == "visual_fragment" and "visual_context" in visual.semantic_flags:
                visuals.setdefault(key, visual)
    questions = []
    if row.options["grouping"] == "copies":
        selected = {}
        for item in items:
            if item.number not in selected or item.confidence > selected[item.number].confidence:
                selected[item.number] = item
        items = list(selected.values())
    for index, item in enumerate(items):
        key = (item.source_name, item.number)
        crop = crops.get(key)
        page_index = page_indexes.get((item.source_name, crop.page_number)) if crop else None
        if page_index is None:
            candidates = [i for (name, _), i in page_indexes.items() if name == item.source_name]
            page_index = candidates[0] if len(candidates) == 1 else None
        question = {
            **asdict(item), "id": f"q{index + 1}", "number": index + 1, "source_number": item.number,
            "page_index": page_index, "reviewed": False, "crop": None,
            "original": {"prompt": item.prompt, "choices": item.choices, "answer": item.answer, "explanation": item.explanation},
        }
        visual = visuals.get(key)
        if visual:
            question["visual"] = _put(row, f"figure-{index + 1}.png", visual.data, visual.mime,
                                       width=visual.width_px, height=visual.height_px)
        questions.append(question)
    document = {"questions": questions, "pages": pages, "warnings": collection.warnings,
                "source_count": len(row.source_files)}
    return _save(job, document=document, stage="review")


def _question_image(row, question, *, prefer_page=False):
    from PIL import Image
    page_index = question.get("page_index")
    if prefer_page and isinstance(page_index, int):
        page = row.document["pages"][page_index]
        return _read(row, page), page["mime"]
    if question.get("crop") and isinstance(page_index, int):
        page = row.document["pages"][page_index]
        with Image.open(io.BytesIO(_read(row, page))) as image:
            x, y, width, height = question["crop"]
            box = (int(x * image.width), int(y * image.height),
                   min(image.width, math.ceil((x + width) * image.width)),
                   min(image.height, math.ceil((y + height) * image.height)))
            output = io.BytesIO()
            image.crop(box).convert("RGB").save(output, format="PNG")
            return output.getvalue(), "image/png"
    asset = question.get("visual")
    if asset:
        return _read(row, asset), asset["mime"]
    if isinstance(page_index, int):
        page = row.document["pages"][page_index]
        return _read(row, page), page["mime"]
    return None, None


def _review_initial(job, row):
    from apps.domains.tools.problem_studio.conversion_ai import propose_question, redraw_figure
    document = copy.deepcopy(row.document)
    for question in document["questions"]:
        if question.get("analysis_complete"):
            continue
        if row.options["level"] != "questions":
            image, mime = _question_image(row, question, prefer_page=True)
            proposal = propose_question(question, image, mime, instruction="원문의 정답·해설을 대조하고 없으면 풀이를 작성하세요.", thorough=row.options["level"] == "thorough")
            question["answer"] = proposal["answer"]
            question["explanation"] = proposal["explanation"]
            question["review_flags"] = list(dict.fromkeys([*question.get("review_flags", []), *proposal.get("review_flags", []), "AI 정답·해설 대조 필요"]))
        if row.options.get("redraw") and question.get("visual"):
            image, mime = _question_image(row, question)
            data = redraw_figure(image, mime)
            question["redrawn"] = _put(row, f"redrawn-{question['id']}.png", data, "image/png")
            question["review_flags"] = [*question.get("review_flags", []), "다시 그린 그림을 원본과 대조해 주세요."]
        question["analysis_complete"] = True
        row = _save(job, document=document, stage="review")
    return row


def _build(job, row):
    from PIL import Image
    from apps.domains.tools.problem_studio.hwpx_writer import build_hwpx_exam_document
    from apps.domains.tools.problem_studio.document_style import resolve_document_style_payload
    row = _save(job, stage="document")
    questions = row.document["questions"]
    if row.options.get("require_review") and any(not q.get("reviewed") for q in questions):
        return row
    style = resolve_document_style_payload(tenant=row.tenant, user=row.owner, payload={})["_resolved_document_style"]
    visuals = {}
    for question in questions:
        asset = question.get("redrawn") or question.get("visual")
        if question.get("crop") and not question.get("redrawn"):
            data, mime = _question_image(row, question)
        elif asset:
            data, mime = _read(row, asset), asset["mime"]
        else:
            continue
        with Image.open(io.BytesIO(data)) as image:
            visuals[question["number"]] = {"data": data, "mime": mime, "width_px": image.width, "height_px": image.height}
    artifacts = {}
    review_label = "검수 완료" if all(q.get("reviewed") for q in questions) else "검수용 초안"
    for kind, label, layout, per_page in (
        ("manuscript", "원고본", {"page_width_mm": 210, "page_height_mm": 297, "column_count": 1}, 0),
        ("arranged", "배열본", {"page_width_mm": 210, "page_height_mm": 297, "column_count": 1}, 4),
        ("a3", "평가원 A3", {"page_width_mm": 297, "page_height_mm": 420, "column_count": 2, "center_line": True}, 0),
    ):
        data = build_hwpx_exam_document(
            title=row.title, meta_lines=[f"{label} · {len(questions)}문항 · {review_label}"],
            items=questions, document_style={**style, "page_layout": layout}, question_visuals=visuals,
            questions_per_page=per_page, append_solutions=row.options["level"] != "questions",
        )
        artifacts[kind] = _put(row, f"{label}.hwpx", data, "application/hwp+zip")
    return _save(job, artifacts=artifacts, artifact_version=row.version, stage="finish")


def handle_conversion_job(job):
    row = None
    try:
        row = _query(job).first()
        if row is None:
            return AIResult.failed(job.id, "Conversion not found")
        if row.status in {"REVIEW", "DONE"}:
            return AIResult.done(job.id, {"conversion_id": str(row.id)})
        row = _save(job, status="RUNNING", error="")
        action = row.operation["action"]
        if action == "convert":
            if not row.document.get("questions"):
                row = _extract(job, row)
            row = _review_initial(job, row)
            row = _build(job, row)
        elif action == "rebuild":
            row = _build(job, row)
        else:
            from apps.domains.tools.problem_studio.conversion_ai import propose_question, redraw_figure
            document = copy.deepcopy(row.document)
            question = next(q for q in document["questions"] if q["id"] == row.operation["question_id"])
            data, mime = _question_image(row, question, prefer_page=action != "redraw")
            if action == "redraw":
                image = redraw_figure(data, mime)
                proposal = {"redrawn": _put(row, f"proposal-{question['id']}.png", image, "image/png")}
            else:
                instruction = row.operation.get("instruction") or "원본과 다시 대조하여 문항, 정답, 해설의 오류를 바로잡으세요."
                proposal = propose_question(question, data, mime, instruction=instruction, thorough=True)
            question["proposal"] = proposal
            question["reviewed"] = False
            row = _save(job, document=document)
        state = "REVIEW" if any(not q.get("reviewed") or q.get("proposal") for q in row.document.get("questions", [])) else "DONE"
        final = {"status": state, "stage": "finish", "error": ""}
        if action in {"correct", "reanalyze", "redraw"}:
            final["version"] = row.version + 1
        _save(job, **final)
        return AIResult.done(job.id, {"conversion_id": str(row.id)})
    except SupersededConversion:
        return AIResult.failed(job.id, "Conversion operation was superseded")
    except Exception:
        logger.exception("Conversion failed job=%s tenant=%s", job.id, job.tenant_id)
        try:
            if row is not None:
                _save(job, status="FAILED", error="작업을 완료하지 못했습니다. 원본과 저장된 수정 내용은 보관돼 있습니다. 이어서 하기로 다시 시도해 주세요.")
        except (SupersededConversion, ValueError):
            pass
        return AIResult.failed(job.id, "Conversion failed; saved checkpoints are available")
