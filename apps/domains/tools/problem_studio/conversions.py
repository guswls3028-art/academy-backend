"""Persistent conversion/review contract. Only the owning teacher can mutate a draft."""
from __future__ import annotations

import copy
import math
import uuid

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.domains.tools.problem_studio.models import ProblemStudioConversion
from apps.support.tools.ai_dependencies import dispatch_tools_ai_job


LEVELS = {"questions", "explanations", "thorough"}
ACTIVE = {"PENDING", "RUNNING"}
FIELDS = {"prompt": 12000, "answer": 2000, "explanation": 16000}


def normalize_options(raw):
    if not isinstance(raw, dict) or raw.get("external_ai_confirmed") is not True:
        raise ValidationError("원본의 외부 AI 처리를 확인해 주세요.")
    level = raw.get("level", "explanations")
    grouping = raw.get("grouping", "separate")
    if level not in LEVELS or grouping not in {"separate", "copies"}:
        raise ValidationError("변환 수준과 파일 구성을 확인해 주세요.")
    return {
        "level": level, "grouping": grouping,
        "redraw": raw.get("redraw") is True,
        "require_review": raw.get("require_review") is True,
        "external_ai_confirmed": True,
        "subject": str(raw.get("subject") or "")[:40],
    }


def validate_question(raw, original):
    if not isinstance(raw, dict):
        raise ValidationError("문항 내용을 확인해 주세요.")
    result = copy.deepcopy(original)
    for key, limit in FIELDS.items():
        if key in raw:
            if not isinstance(raw[key], str) or len(raw[key]) > limit:
                raise ValidationError(f"{key} 내용이 너무 길거나 올바르지 않습니다.")
            result[key] = raw[key]
    if "choices" in raw:
        choices = raw["choices"]
        if not isinstance(choices, list) or len(choices) > 20 or any(not isinstance(c, str) or len(c) > 2000 for c in choices):
            raise ValidationError("보기는 20개 이하의 글로 입력해 주세요.")
        result["choices"] = choices
    if "crop" in raw:
        crop = raw["crop"]
        if crop is not None:
            if not isinstance(crop, list) or len(crop) != 4 or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in crop):
                raise ValidationError("그림 범위를 확인해 주세요.")
            x, y, width, height = crop
            if min(x, y) < 0 or min(width, height) <= 0 or x + width > 1.00001 or y + height > 1.00001:
                raise ValidationError("그림 범위가 원본을 벗어났습니다.")
        result["crop"] = crop
    if "page_index" in raw:
        result["page_index"] = raw["page_index"]
    if result.get("crop") != original.get("crop") or result.get("page_index") != original.get("page_index"):
        result.pop("redrawn", None)
    result["reviewed"] = False
    if original.get("id"):
        result["analysis_complete"] = True
    result.pop("proposal", None)
    return result


def owned(request):
    return ProblemStudioConversion.objects.filter(tenant=request.tenant, owner=request.user)


def serialize_conversion(row, *, detail=True):
    payload = {
        "id": str(row.id), "title": row.title, "status": row.status,
        "stage": row.stage, "version": row.version, "options": row.options,
        "error": row.error, "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(), "artifact_version": row.artifact_version,
        "question_count": len(row.document.get("questions", [])),
        "review_count": sum(not q.get("reviewed") for q in row.document.get("questions", [])),
        "source_files": [{"name": f["name"], "size": f["size"]} for f in row.source_files],
    }
    if not detail:
        return payload
    from apps.infrastructure.storage.r2 import generate_presigned_get_url_storage

    prefix = f"tenants/{row.tenant_id}/tools/problem-studio/conversions/{row.id}/"

    def asset(value):
        if not isinstance(value, dict) or not str(value.get("key", "")).startswith(prefix):
            return None
        return {
            k: v for k, v in value.items() if k != "key"
        } | {"url": generate_presigned_get_url_storage(
            key=value["key"], expires_in=900,
            filename=value.get("filename"), content_type=value.get("mime", "image/png"),
        )}

    document = copy.deepcopy(row.document)
    document["pages"] = [asset(page) for page in document.get("pages", [])]
    for question in document.get("questions", []):
        for name in ("visual", "redrawn"):
            if name in question:
                question[name] = asset(question[name])
        if question.get("proposal", {}).get("redrawn"):
            question["proposal"]["redrawn"] = asset(question["proposal"]["redrawn"])
    payload["document"] = document
    payload["source_archive"] = asset({"key": row.source_archive_key, "filename": "원본.zip", "mime": "application/zip"})
    exportable = row.artifact_version == row.version and not (
        row.options.get("require_review") and payload["review_count"]
    )
    payload["artifacts"] = {name: asset(value) for name, value in row.artifacts.items()} if exportable else {}
    return payload


def queue_conversion(row, operation):
    """Caller holds the row lock. A committed nonce fences duplicate/stale workers."""
    token = uuid.uuid4().hex
    row.operation = {**operation, "token": token, "version": row.version}
    row.status = "PENDING"
    row.job_id = ""
    row.error = ""
    row.save(update_fields=["operation", "status", "job_id", "error", "updated_at"])
    row_id, tenant_id, owner_id = str(row.id), str(row.tenant_id), str(row.owner_id)

    def dispatch():
        try:
            result = dispatch_tools_ai_job(
                job_type="problem_studio_transcription",
                payload={"conversion_id": row_id, "operation_token": token, "tenant_id": tenant_id, "request_user_id": owner_id},
                tenant_id=tenant_id, source_domain="tools_problem_studio_conversion", source_id=row_id, tier="basic",
            )
            query = ProblemStudioConversion.objects.filter(id=row_id, tenant_id=tenant_id, owner_id=owner_id, operation__token=token)
            if result.get("ok"):
                query.update(job_id=result["job_id"])
            else:
                query.update(status="FAILED", error="변환을 시작하지 못했습니다. 잠시 뒤 이어서 하기를 눌러 주세요.")
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Conversion dispatch failed id=%s tenant=%s", row_id, tenant_id)
            ProblemStudioConversion.objects.filter(id=row_id, tenant_id=tenant_id, operation__token=token).update(
                status="FAILED", error="작업 연결에 실패했습니다. 이어서 하기로 다시 시도해 주세요.",
            )

    transaction.on_commit(dispatch)


def refresh_failed_job(row):
    if row.status not in ACTIVE or not row.job_id:
        return row
    from academy.adapters.db.django import repositories_ai as ai_repo
    job = ai_repo.get_job_model_for_status(row.job_id, str(row.tenant_id))
    if job and job.status in {"FAILED", "CANCELLED"}:
        # Readback must remain side-effect free. The next explicit action persists recovery.
        row.status = "FAILED"
        row.error = "작업이 중단됐습니다. 저장된 단계부터 이어서 할 수 있습니다."
    return row
