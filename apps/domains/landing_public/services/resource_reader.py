"""Private preparation and public reading of publisher-owned report originals."""

from copy import deepcopy
from datetime import timedelta
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import uuid

from django.db import transaction
from django.utils import timezone

from apps.domains.landing_public.models.resource import PublicResourceFile
from apps.infrastructure.storage.r2 import (
    delete_object_r2_admin, generate_presigned_get_url_admin,
    get_admin_object_bytes, upload_fileobj_to_r2_admin,
)
from apps.shared.contracts.ai_result import AIResult
from apps.support.landing_public.resource_reader_jobs import create_reader_job, fail_reader_job, publish_reader_job

logger = logging.getLogger(__name__)
READER_TYPES = frozenset({"pdf", "hwp", "hwpx", "docx", "xlsx", "pptx", "png", "jpg", "jpeg", "webp", "gif", "txt", "md"})
READER_ERROR = "본문을 준비하지 못했습니다. 다시 시도하거나 PDF로 저장해 올려주세요. 원본은 보존됩니다."
READER_TIMEOUT = timedelta(minutes=10)


def extension(file):
    return file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""


def reader_state(file):
    # Preserve existing published originals until their derived pages are warmed.
    if extension(file) == "pdf" and file.reader_status in ("", "unprepared"):
        return "ready"
    if extension(file) not in READER_TYPES:
        return "unsupported"
    if file.reader_status in ("pending", "running") and file.reader_requested_at and file.reader_requested_at < timezone.now() - READER_TIMEOUT:
        return "failed"
    if file.reader_status == "running":
        return "pending"
    return file.reader_status or "unprepared"


def needs_page_images(file):
    return extension(file) in ("pdf", "hwp", "hwpx", "docx", "xlsx", "pptx") and not file.reader_data.get("page_images")


def prepare_reader(file):
    """Caller owns publisher authorization; immutable file/job IDs fence retries."""
    with transaction.atomic():
        current = PublicResourceFile.objects.select_for_update().get(pk=file.pk, tenant_id=file.tenant_id)
        if not current.is_ready or current.is_removed:
            return current
        state = reader_state(current)
        if state in ("pending", "unsupported") or (state == "ready" and not needs_page_images(current)):
            return current
        token = uuid.uuid4()
        job = create_reader_job(file_id=str(current.pk), tenant_id=current.tenant_id, token=str(token))
        current.reader_status = "pending"
        current.reader_token = token
        current.reader_requested_at = timezone.now()
        current.save(update_fields=["reader_status", "reader_token", "reader_requested_at"])
    def enqueue():
        try:
            enqueued = publish_reader_job(job)
        except Exception:
            enqueued = False
        if not enqueued:
            PublicResourceFile.objects.filter(pk=current.pk, reader_token=token, reader_status="pending").update(reader_status="failed")
            fail_reader_job(job)
    transaction.on_commit(enqueue)
    current.refresh_from_db()
    return current


def _prefix(file, token):
    return f"landing-public/resources/{file.tenant_id}/{file.pk}/reader/{token}"


def _asset_names(data):
    names = data.get("assets", [])
    if not isinstance(names, list) or len(names) > 101 or any(
        not isinstance(name, str) or not re.fullmatch(r"(?:image-\d+\.(?:webp|gif|png)|pages\.pdf)", name) for name in names
    ):
        raise ValueError("Invalid reader assets")
    return names


def reader_payload(file):
    state = reader_state(file)
    result = {"status": state}
    if state != "ready":
        if state == "failed":
            result["message"] = READER_ERROR
        return result
    if extension(file) == "pdf" and needs_page_images(file):
        return {"status": "ready", "mode": "pages", "blocks": [],
                "pdf_url": generate_presigned_get_url_admin(key=file.storage_key, expires_in=300)}
    data = deepcopy(file.reader_data)
    names = _asset_names(data)
    prefix = _prefix(file, file.reader_token)
    urls = {name: generate_presigned_get_url_admin(key=f"{prefix}/{name}", expires_in=300) for name in names}

    def link(blocks, depth=0):
        if depth > 10:
            raise ValueError("Reader nesting limit")
        for block in blocks:
            if block.get("kind") == "image":
                block["url"] = urls[block.pop("asset")]
            elif block.get("kind") == "table":
                for row in block["rows"]:
                    for cell in row:
                        link(cell, depth + 1)
    link(data.get("blocks", []))
    if data.get("pdf"):
        result["pdf_url"] = urls[data["pdf"]]
    result.update({"mode": data.get("mode", "article"), "blocks": data.get("blocks", []), "pages": data.get("pages")})
    return result


def delete_reader_objects(file):
    for key in file.reader_object_keys:
        expected = f"landing-public/resources/{file.tenant_id}/{file.pk}/reader/"
        if not key.startswith(expected) or not re.fullmatch(r"[0-9a-f-]{36}/(?:image-\d+\.(?:webp|gif|png)|pages\.pdf)", key[len(expected):]):
            raise ValueError("Reader cleanup scope mismatch")
        delete_object_r2_admin(key=key)


def _run_renderer(command, *, env, cwd):
    with subprocess.Popen(command, env=env, cwd=cwd, start_new_session=True,
                          stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL) as process:
        try:
            code = process.wait(timeout=150)
        except BaseException:
            # Stop the native child too; killing only Python can leave an orphan.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            raise
        if code:
            raise subprocess.CalledProcessError(code, command)


def handle_public_resource_reader_job(job):
    source_id = str(job.source_id or "")
    try:
        token = uuid.UUID(str((job.payload or {}).get("token", "")))
        file_id = uuid.UUID(source_id)
        tenant_id = int(str(job.tenant_id))
    except (ValueError, TypeError):
        return AIResult.failed(job.id, "Invalid resource reader scope")
    if job.source_domain != "landing_public_resource" or str(job.id) != f"resource-reader-{token}":
        return AIResult.failed(job.id, "Invalid resource reader contract")
    queryset = PublicResourceFile.objects.filter(pk=file_id, tenant_id=tenant_id, reader_token=token,
                                                is_ready=True, is_removed=False)
    with transaction.atomic():
        file = queryset.select_for_update(of=("self",)).select_related("post").first()
        if not file or file.reader_status != "pending" or (file.post_id and file.post.status != "published"):
            return AIResult.done(job.id, {"outcome": "obsolete"})
        # A duplicate SQS delivery must never delete a successful sibling's files.
        file.reader_status = "running"
        file.save(update_fields=["reader_status"])
    prefix = _prefix(file, token)
    keys = []
    try:
        if file.storage_key != f"landing-public/resources/{file.tenant_id}/{file.pk}":
            raise ValueError("Resource storage scope mismatch")
        stored = get_admin_object_bytes(key=file.storage_key, max_bytes=30 * 1024 * 1024, timeout_seconds=15)
        if not stored or len(stored[0]) != file.size:
            raise ValueError("Missing resource original")
        with tempfile.TemporaryDirectory(prefix="resource-reader-") as temp:
            directory = Path(temp)
            source = directory / f"source.{extension(file)}"
            source.write_bytes(stored[0])
            output = directory / "output"
            environment = {"PATH": os.defpath, "LANG": "C.UTF-8", "HOME": temp,
                           "PYTHONPATH": os.pathsep.join(dict.fromkeys([
                               str(Path(__file__).resolve().parents[4]),
                               *[entry for entry in sys.path if entry and Path(entry).is_absolute()],
                           ]))}
            # Explicit deployment/test overrides are paths, never credentials.
            for name in ("RESOURCE_RHWP_BINARY", "RESOURCE_READER_FONT_PATH"):
                if os.environ.get(name):
                    environment[name] = os.environ[name]
            _run_renderer([sys.executable, "-m", "apps.infrastructure.storage.resource_document_renderer",
                           str(source), str(output), extension(file)], env=environment, cwd=temp)
            manifest = output / "manifest.json"
            if manifest.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("Reader manifest limit")
            data = json.loads(manifest.read_text())
            names = _asset_names(data)
            keys = [f"{prefix}/{name}" for name in names]
            # Record exact cleanup targets before writes; retries retain every generation.
            with transaction.atomic():
                current = queryset.select_for_update(of=("self",)).first()
                if not current or current.reader_status != "running":
                    return AIResult.done(job.id, {"outcome": "obsolete"})
                current.reader_object_keys = list(dict.fromkeys([*current.reader_object_keys, *keys]))
                current.save(update_fields=["reader_object_keys"])
            for name, key in zip(names, keys):
                # Serialize one bounded object write with deletion/retry so cleanup
                # cannot finish before a late object write from an old generation.
                with transaction.atomic():
                    if not queryset.select_for_update(of=("self",)).filter(reader_status="running").first():
                        raise ValueError("Obsolete resource reader")
                    with (output / name).open("rb") as stream:
                        upload_fileobj_to_r2_admin(fileobj=stream, key=key,
                            content_type={"pdf": "application/pdf", "webp": "image/webp", "gif": "image/gif", "png": "image/png"}[name.rsplit(".", 1)[-1]])
            if not queryset.filter(reader_status="running").update(reader_status="ready", reader_data=data):
                raise ValueError("Obsolete resource reader")
        return AIResult.done(job.id, {"outcome": "ready"})
    except Exception:
        # No document text, native stderr or storage credentials enter logs.
        logger.warning("Public reader preparation failed: file=%s job=%s", file_id, job.id)
        queryset.filter(reader_status="running").update(reader_status="failed")
        for key in keys:
            try:
                delete_object_r2_admin(key=key)
            except Exception:
                logger.warning("Public reader cleanup needs retry: file=%s job=%s", file_id, job.id)
        return AIResult.done(job.id, {"outcome": "failed"})
