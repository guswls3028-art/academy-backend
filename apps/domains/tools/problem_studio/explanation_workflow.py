from __future__ import annotations

import io
import json
import logging
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from django.db import transaction

from apps.core.models import Tenant
from apps.domains.tools.problem_studio.async_transfer import source_files_from_archive
from apps.domains.tools.problem_studio.beta_access import BETA_FREE_RUN_LIMIT
from apps.domains.tools.problem_studio.models import ProblemStudioBetaRun
from apps.shared.contracts.ai_job import AIJob
from apps.shared.contracts.ai_result import AIResult
from apps.support.tools.ai_dependencies import dispatch_tools_ai_job


logger = logging.getLogger(__name__)

EXPLANATION_BATCH_SIZE = 10
EXPLANATION_MAX_RETRIES = 2
_ATTENTION_STATUSES = {
    "ai_mismatch",
    "source_reference_ai_mismatch",
    "solve_validation_failed",
    "verification_failed",
}


class ProblemStudioExplanationResumeUnavailable(Exception):
    pass


def _run_prefix(run: ProblemStudioBetaRun) -> str:
    return f"tenants/{run.tenant_id}/tools/problem-studio/explanation-runs/{run.id}"


def _temp_run_prefix(run: ProblemStudioBetaRun) -> str:
    return f"tenants/{run.tenant_id}/tools/problem-studio/tmp/explanation-runs/{run.id}"


def _expected_temp_prefix(tenant_id: str) -> str:
    return f"tenants/{tenant_id}/tools/problem-studio/tmp/explanation-runs/"


def _safe_result_name(source_name: str) -> str:
    stem = Path(source_name or "문제집").stem.strip(" .") or "문제집"
    for value in '\\/:*?"<>|':
        stem = stem.replace(value, "-")
    return f"{stem[:160]}_정답해설_Beta.pdf"


def _download_to(*, key: str, target: Path, job_id: str) -> None:
    from academy.adapters.ai.storage.downloader import download_r2_key_to_tmp

    downloaded = Path(download_r2_key_to_tmp(r2_key=key, job_id=job_id))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(downloaded, target)
    finally:
        parent = downloaded.parent
        if parent.name.startswith("ai-job-"):
            shutil.rmtree(parent, ignore_errors=True)


def _upload_path(*, path: Path, key: str, content_type: str) -> None:
    from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage

    with path.open("rb") as handle:
        upload_fileobj_to_r2_storage(
            fileobj=handle,
            key=key,
            content_type=content_type,
        )


def _upload_json(*, payload: dict[str, Any], key: str) -> None:
    from apps.infrastructure.storage.r2 import upload_fileobj_to_r2_storage

    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    upload_fileobj_to_r2_storage(
        fileobj=io.BytesIO(data),
        key=key,
        content_type="application/json; charset=utf-8",
    )


def _zip_checkpoint(*, work_dir: Path, target: Path) -> None:
    with zipfile.ZipFile(
        target,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        allowZip64=True,
    ) as archive:
        for source in sorted(work_dir.rglob("*")):
            if not source.is_file() or source.name == "solutions.json":
                continue
            archive.write(source, source.relative_to(work_dir).as_posix())


def _restore_checkpoint(*, checkpoint: Path, work_dir: Path) -> None:
    with zipfile.ZipFile(checkpoint) as archive:
        root = work_dir.resolve()
        for member in archive.infolist():
            target = (work_dir / member.filename).resolve()
            if target != root and root not in target.parents:
                raise ValueError("Problem Studio 체크포인트 경로가 올바르지 않습니다.")
        archive.extractall(work_dir)


def _solution_metrics(state: dict[str, Any]) -> tuple[int, int, int]:
    values = [value for value in (state.get("items") or {}).values() if isinstance(value, dict)]
    completed = len(values)
    verified = sum(bool(value.get("verification_status")) for value in values)
    attention = sum(str(value.get("verification_status") or "") in _ATTENTION_STATUSES for value in values)
    return completed, verified, attention


def _dispatch_step_locked(
    *,
    run: ProblemStudioBetaRun,
    stage: str,
    cursor: int,
    force_rerun: bool = False,
) -> str:
    result = dispatch_tools_ai_job(
        job_type="problem_studio_transcription",
        payload={
            "explanation_run_id": str(run.id),
            "explanation_stage": stage,
            "tenant_id": str(run.tenant_id),
            "request_user_id": str(run.requested_by_id or ""),
        },
        tenant_id=str(run.tenant_id),
        source_domain="tools_problem_studio",
        source_id=str(run.id),
        tier="basic",
        idempotency_key=f"problem-studio-explanation:{run.id}:{stage}:{cursor}",
        force_rerun=force_rerun,
        rerun_reason="teacher_resume_after_system_failure" if force_rerun else None,
    )
    if not result.get("ok") or not result.get("job_id"):
        raise RuntimeError(result.get("error") or "정답·해설 작업을 예약할 수 없습니다.")
    run.stage = stage
    run.job_id = str(result["job_id"])
    run.last_error = ""
    run.release_reason = ""
    run.save(update_fields=["stage", "job_id", "last_error", "release_reason", "updated_at"])
    return run.job_id


@transaction.atomic
def start_explanation_workflow(*, run_id: str, tenant_id: str) -> str:
    run = ProblemStudioBetaRun.objects.select_for_update().get(
        pk=run_id,
        tenant_id=tenant_id,
    )
    if run.status != ProblemStudioBetaRun.Status.RESERVED:
        raise RuntimeError("정답·해설 Beta 예약이 유효하지 않습니다.")
    if not run.source_archive_key.startswith(_expected_temp_prefix(str(run.tenant_id))):
        raise RuntimeError("정답·해설 원본 저장 경로가 올바르지 않습니다.")
    return _dispatch_step_locked(run=run, stage=run.stage, cursor=0)


@transaction.atomic
def resume_explanation_workflow(
    *,
    run_id: str,
    tenant: Any,
    user: Any,
) -> str:
    Tenant.objects.select_for_update().get(pk=tenant.pk)
    run = (
        ProblemStudioBetaRun.objects.select_for_update()
        .filter(pk=run_id, tenant=tenant, requested_by=user)
        .first()
    )
    if run is None:
        raise ProblemStudioExplanationResumeUnavailable("재개할 정답·해설 작업을 찾을 수 없습니다.")
    if run.status == ProblemStudioBetaRun.Status.COMPLETED:
        raise ProblemStudioExplanationResumeUnavailable("이미 완료된 정답·해설 작업입니다.")
    if run.status == ProblemStudioBetaRun.Status.RESERVED and run.job_id:
        return run.job_id
    if not run.source_archive_key.startswith(_expected_temp_prefix(str(run.tenant_id))):
        raise ProblemStudioExplanationResumeUnavailable("재개할 원본 파일이 남아 있지 않습니다.")
    active = (
        ProblemStudioBetaRun.objects.filter(
            tenant=tenant,
            status__in=[
                ProblemStudioBetaRun.Status.RESERVED,
                ProblemStudioBetaRun.Status.COMPLETED,
            ],
        )
        .exclude(pk=run.pk)
        .count()
    )
    if active >= BETA_FREE_RUN_LIMIT:
        raise ProblemStudioExplanationResumeUnavailable("문제집 해설 Beta 무료 체험 3회를 모두 사용했습니다.")
    run.status = ProblemStudioBetaRun.Status.RESERVED
    run.job_id = ""
    run.release_reason = ""
    run.last_error = ""
    run.save(update_fields=["status", "job_id", "release_reason", "last_error", "updated_at"])
    cursor = (
        run.completed_question_count
        if run.stage == ProblemStudioBetaRun.Stage.SOLVE
        else run.verified_question_count
        if run.stage == ProblemStudioBetaRun.Stage.VERIFY
        else 0
    )
    return _dispatch_step_locked(
        run=run,
        stage=run.stage,
        cursor=cursor,
        force_rerun=True,
    )


@transaction.atomic
def _schedule_next(
    *,
    run_id: str,
    tenant_id: str,
    current_job_id: str,
    stage: str,
    cursor: int,
    updates: dict[str, Any] | None = None,
) -> None:
    run = ProblemStudioBetaRun.objects.select_for_update().get(
        pk=run_id,
        tenant_id=tenant_id,
    )
    if run.status != ProblemStudioBetaRun.Status.RESERVED or run.job_id != current_job_id:
        return
    for field, value in (updates or {}).items():
        setattr(run, field, value)
    if updates:
        run.save(update_fields=[*updates.keys(), "updated_at"])
    _dispatch_step_locked(run=run, stage=stage, cursor=cursor)


@transaction.atomic
def _mark_failed(*, run_id: str, tenant_id: str, current_job_id: str, error: str) -> None:
    run = ProblemStudioBetaRun.objects.select_for_update().filter(
        pk=run_id,
        tenant_id=tenant_id,
    ).first()
    if run is None or run.job_id != current_job_id:
        return
    run.status = ProblemStudioBetaRun.Status.RELEASED
    run.job_id = ""
    run.last_error = str(error or "system_failure")[:2000]
    run.release_reason = "system_failure"
    run.save(
        update_fields=[
            "status",
            "job_id",
            "last_error",
            "release_reason",
            "updated_at",
        ]
    )


def _load_current_run(job: AIJob) -> ProblemStudioBetaRun | None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    run_id = str(payload.get("explanation_run_id") or "")
    tenant_id = str(job.tenant_id or "")
    requested_by_id = str(payload.get("request_user_id") or "")
    if not run_id or not tenant_id or not requested_by_id:
        return None
    run = ProblemStudioBetaRun.objects.filter(
        pk=run_id,
        tenant_id=tenant_id,
        requested_by_id=requested_by_id,
    ).first()
    if (
        run is None
        or run.status != ProblemStudioBetaRun.Status.RESERVED
        or run.job_id != str(job.id)
        or run.stage != str(payload.get("explanation_stage") or "")
    ):
        return None
    return run


def _extract_step(*, run: ProblemStudioBetaRun, job: AIJob) -> dict[str, Any]:
    from scripts.problem_studio_pdf_prototype import extract_manifest

    with tempfile.TemporaryDirectory(prefix=f"problem-studio-extract-{run.id}-") as temporary:
        root = Path(temporary)
        archive_path = root / "sources.zip"
        work_dir = root / "checkpoint"
        checkpoint_path = root / "checkpoint.zip"
        _download_to(key=run.source_archive_key, target=archive_path, job_id=f"{job.id}-source")
        with source_files_from_archive(archive_path) as source_files:
            if len(source_files) != 1 or Path(source_files[0].name).suffix.lower() != ".pdf":
                raise ValueError("정답·해설 PDF Beta는 한 번에 PDF 한 파일만 처리합니다.")
            source_path = root / "source.pdf"
            shutil.copyfile(source_files[0].path, source_path)
            manifest = extract_manifest(source=source_path, work_dir=work_dir)

        checkpoint_key = f"{_temp_run_prefix(run)}/checkpoint.zip"
        solutions_key = f"{_temp_run_prefix(run)}/solutions.json"
        _zip_checkpoint(work_dir=work_dir, target=checkpoint_path)
        _upload_path(path=checkpoint_path, key=checkpoint_key, content_type="application/zip")
        _upload_json(
            payload={"schema": "problem-studio-solutions/v1", "items": {}},
            key=solutions_key,
        )
        question_count = int(manifest["metrics"]["question_count"])
        _schedule_next(
            run_id=str(run.id),
            tenant_id=str(run.tenant_id),
            current_job_id=str(job.id),
            stage=ProblemStudioBetaRun.Stage.SOLVE,
            cursor=0,
            updates={
                "source_name": source_files[0].name,
                "checkpoint_key": checkpoint_key,
                "solutions_key": solutions_key,
                "question_count": question_count,
                "completed_question_count": 0,
                "verified_question_count": 0,
                "review_required_count": 0,
            },
        )
        return {"question_count": question_count, "next_stage": "solve"}


def _restore_work_dir(
    *,
    run: ProblemStudioBetaRun,
    job: AIJob,
    root: Path,
) -> Path:
    if not run.checkpoint_key.startswith(_expected_temp_prefix(str(run.tenant_id))):
        raise RuntimeError("정답·해설 체크포인트 경로가 올바르지 않습니다.")
    if not run.solutions_key.startswith(_expected_temp_prefix(str(run.tenant_id))):
        raise RuntimeError("정답·해설 풀이 저장 경로가 올바르지 않습니다.")
    checkpoint_path = root / "checkpoint.zip"
    work_dir = root / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    _download_to(key=run.checkpoint_key, target=checkpoint_path, job_id=f"{job.id}-checkpoint")
    _restore_checkpoint(checkpoint=checkpoint_path, work_dir=work_dir)
    _download_to(key=run.solutions_key, target=work_dir / "solutions.json", job_id=f"{job.id}-solutions")
    return work_dir


def _solve_step(*, run: ProblemStudioBetaRun, job: AIJob) -> dict[str, Any]:
    from academy.adapters.ai.config import AIConfig
    from scripts.problem_studio_pdf_prototype import _load_json, solve_manifest

    with tempfile.TemporaryDirectory(prefix=f"problem-studio-solve-{run.id}-") as temporary:
        root = Path(temporary)
        work_dir = _restore_work_dir(run=run, job=job, root=root)
        manifest = _load_json(work_dir / "manifest.json", {})
        cfg = AIConfig.load()
        request_payload = run.request_payload if isinstance(run.request_payload, dict) else {}
        state = solve_manifest(
            manifest=manifest,
            work_dir=work_dir,
            batch_size=EXPLANATION_BATCH_SIZE,
            max_retries=EXPLANATION_MAX_RETRIES,
            limit=EXPLANATION_BATCH_SIZE,
            blank_bedrock_model=cfg.PROBLEM_GEN_BEDROCK_MODEL,
            blank_bedrock_region=cfg.BEDROCK_REGION,
            subject=str(request_payload.get("subject") or ""),
            note_policy=str(request_payload.get("note_policy") or ""),
        )
        completed, verified, attention = _solution_metrics(state)
        _upload_path(
            path=work_dir / "solutions.json",
            key=run.solutions_key,
            content_type="application/json; charset=utf-8",
        )
        next_stage = (
            ProblemStudioBetaRun.Stage.VERIFY
            if completed >= run.question_count
            else ProblemStudioBetaRun.Stage.SOLVE
        )
        _schedule_next(
            run_id=str(run.id),
            tenant_id=str(run.tenant_id),
            current_job_id=str(job.id),
            stage=next_stage,
            cursor=completed if next_stage == ProblemStudioBetaRun.Stage.SOLVE else verified,
            updates={
                "completed_question_count": completed,
                "verified_question_count": verified,
                "review_required_count": attention,
            },
        )
        return {"completed": completed, "question_count": run.question_count, "next_stage": next_stage}


def _verify_step(*, run: ProblemStudioBetaRun, job: AIJob) -> dict[str, Any]:
    from scripts.problem_studio_pdf_prototype import _load_json, verify_solutions

    with tempfile.TemporaryDirectory(prefix=f"problem-studio-verify-{run.id}-") as temporary:
        root = Path(temporary)
        work_dir = _restore_work_dir(run=run, job=job, root=root)
        manifest = _load_json(work_dir / "manifest.json", {})
        request_payload = run.request_payload if isinstance(run.request_payload, dict) else {}
        state = verify_solutions(
            manifest=manifest,
            work_dir=work_dir,
            batch_size=EXPLANATION_BATCH_SIZE,
            max_retries=EXPLANATION_MAX_RETRIES,
            limit=EXPLANATION_BATCH_SIZE,
            ai_generated_only=True,
            subject=str(request_payload.get("subject") or ""),
        )
        completed, verified, attention = _solution_metrics(state)
        _upload_path(
            path=work_dir / "solutions.json",
            key=run.solutions_key,
            content_type="application/json; charset=utf-8",
        )
        ai_generated = [
            value
            for value in (state.get("items") or {}).values()
            if isinstance(value, dict)
            and value.get("answer_source") == "ai_generated"
            and value.get("verification_status") != "solve_validation_failed"
        ]
        verified_ai = sum(bool(value.get("verification_status")) for value in ai_generated)
        next_stage = (
            ProblemStudioBetaRun.Stage.BUILD
            if verified_ai >= len(ai_generated)
            else ProblemStudioBetaRun.Stage.VERIFY
        )
        _schedule_next(
            run_id=str(run.id),
            tenant_id=str(run.tenant_id),
            current_job_id=str(job.id),
            stage=next_stage,
            cursor=verified_ai,
            updates={
                "completed_question_count": completed,
                "verified_question_count": verified,
                "review_required_count": attention,
            },
        )
        return {"verified": verified_ai, "verify_total": len(ai_generated), "next_stage": next_stage}


@transaction.atomic
def _complete_run(
    *,
    run_id: str,
    tenant_id: str,
    current_job_id: str,
    result_key: str,
    result_filename: str,
    result_payload: dict[str, Any],
) -> None:
    run = ProblemStudioBetaRun.objects.select_for_update().get(
        pk=run_id,
        tenant_id=tenant_id,
    )
    if run.status != ProblemStudioBetaRun.Status.RESERVED or run.job_id != current_job_id:
        return
    run.status = ProblemStudioBetaRun.Status.COMPLETED
    run.stage = ProblemStudioBetaRun.Stage.DONE
    run.job_id = current_job_id
    run.result_key = result_key
    run.result_filename = result_filename
    run.result_payload = result_payload
    run.last_error = ""
    run.release_reason = ""
    run.save(
        update_fields=[
            "status",
            "stage",
            "job_id",
            "result_key",
            "result_filename",
            "result_payload",
            "last_error",
            "release_reason",
            "updated_at",
        ]
    )


def _cleanup_completed_artifacts(*, run_id: str, tenant_id: str) -> None:
    from apps.infrastructure.storage.r2 import delete_object_r2_storage

    run = ProblemStudioBetaRun.objects.filter(pk=run_id, tenant_id=tenant_id).first()
    if run is None or run.status != ProblemStudioBetaRun.Status.COMPLETED:
        return
    cleared: list[str] = []
    for field in ("source_archive_key", "checkpoint_key", "solutions_key"):
        key = str(getattr(run, field) or "")
        if not key.startswith(_expected_temp_prefix(str(run.tenant_id))):
            continue
        try:
            delete_object_r2_storage(key=key)
            cleared.append(field)
        except Exception:
            logger.warning("PROBLEM_STUDIO_EXPLANATION_CLEANUP_FAILED run_id=%s key=%s", run.id, key, exc_info=True)
    if cleared:
        ProblemStudioBetaRun.objects.filter(
            pk=run.id,
            tenant_id=tenant_id,
        ).update(**{field: "" for field in cleared})


def _build_step(*, run: ProblemStudioBetaRun, job: AIJob) -> dict[str, Any]:
    from scripts.problem_studio_pdf_prototype import _load_json, build_output_pdf

    with tempfile.TemporaryDirectory(prefix=f"problem-studio-build-{run.id}-") as temporary:
        root = Path(temporary)
        work_dir = _restore_work_dir(run=run, job=job, root=root)
        archive_path = root / "sources.zip"
        _download_to(key=run.source_archive_key, target=archive_path, job_id=f"{job.id}-source")
        with source_files_from_archive(archive_path) as source_files:
            if len(source_files) != 1:
                raise ValueError("정답·해설 원본 PDF를 복구할 수 없습니다.")
            source_path = root / "source.pdf"
            shutil.copyfile(source_files[0].path, source_path)

        manifest = _load_json(work_dir / "manifest.json", {})
        result_filename = _safe_result_name(run.source_name or source_files[0].name)
        output_path = root / result_filename
        report = build_output_pdf(
            source=source_path,
            output=output_path,
            work_dir=work_dir,
            manifest=manifest,
            allow_incomplete=False,
        )
        result_key = f"{_run_prefix(run)}/result/{result_filename}"
        _upload_path(path=output_path, key=result_key, content_type="application/pdf")
        result_payload = {
            key: value
            for key, value in report.items()
            if key not in {"output_path"}
        }
        result_payload.update({
            "filename": result_filename,
            "size_bytes": output_path.stat().st_size,
            "review_required": True,
            "review_required_count": run.review_required_count,
            "beta": {"label": "Beta", "free_trial": True, "review_required": True},
        })
        _complete_run(
            run_id=str(run.id),
            tenant_id=str(run.tenant_id),
            current_job_id=str(job.id),
            result_key=result_key,
            result_filename=result_filename,
            result_payload=result_payload,
        )
    _cleanup_completed_artifacts(run_id=str(run.id), tenant_id=str(run.tenant_id))
    return {"result_key": result_key, "filename": result_filename, "next_stage": "done"}


def handle_problem_studio_explanation_step(job: AIJob) -> AIResult:
    payload = job.payload if isinstance(job.payload, dict) else {}
    run_id = str(payload.get("explanation_run_id") or "")
    run = _load_current_run(job)
    if run is None:
        return AIResult.done(job.id, {"run_id": run_id, "superseded": True})
    try:
        if run.stage == ProblemStudioBetaRun.Stage.EXTRACT:
            result = _extract_step(run=run, job=job)
        elif run.stage == ProblemStudioBetaRun.Stage.SOLVE:
            result = _solve_step(run=run, job=job)
        elif run.stage == ProblemStudioBetaRun.Stage.VERIFY:
            result = _verify_step(run=run, job=job)
        elif run.stage == ProblemStudioBetaRun.Stage.BUILD:
            result = _build_step(run=run, job=job)
        else:
            return AIResult.done(job.id, {"run_id": run_id, "superseded": True})
        return AIResult.done(job.id, {"run_id": run_id, **result})
    except Exception as exc:
        logger.exception(
            "PROBLEM_STUDIO_EXPLANATION_STEP_FAILED run_id=%s job_id=%s stage=%s",
            run_id,
            job.id,
            run.stage,
        )
        _mark_failed(
            run_id=run_id,
            tenant_id=str(run.tenant_id),
            current_job_id=str(job.id),
            error=str(exc),
        )
        return AIResult.failed(job.id, str(exc)[:2000])


def is_explanation_step_payload(payload: Any) -> bool:
    return isinstance(payload, dict) and bool(payload.get("explanation_run_id"))


def settle_explanation_step_failure(
    *,
    job_id: str,
    status: str,
    payload: Any,
    error: str = "",
) -> None:
    if status == "DONE" or not is_explanation_step_payload(payload):
        return
    run_id = str(payload.get("explanation_run_id") or "")
    tenant_id = str(payload.get("tenant_id") or "")
    if run_id and tenant_id:
        _mark_failed(
            run_id=run_id,
            tenant_id=tenant_id,
            current_job_id=str(job_id),
            error=error or status or "system_failure",
        )
