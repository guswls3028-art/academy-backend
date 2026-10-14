"""Persistent conversion/review contract. Only the owning teacher can mutate a draft."""
from __future__ import annotations

import copy
import uuid

from django.db import transaction
from rest_framework.exceptions import ValidationError

from apps.domains.tools.problem_studio.conversion_question import validate_question as validate_question_content
from apps.domains.tools.problem_studio.models import ProblemStudioConversion
from apps.support.tools.ai_dependencies import dispatch_tools_ai_job


LEVELS = {"questions", "explanations", "thorough"}
ACTIVE = {"PENDING", "RUNNING"}


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
    try:
        return validate_question_content(raw, original)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


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
