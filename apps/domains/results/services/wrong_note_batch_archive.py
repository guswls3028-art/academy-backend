"""Assemble the class archive in the Tools worker before its last document completes."""

import re
import tempfile
import zipfile

from apps.domains.results.models import WrongNotePDF
from apps.domains.results.services.wrong_note_batch_service import current_documents
from apps.domains.results.services.wrong_note_pdf_service import (
    WrongNotePDFLimitError, wrong_note_pdf_storage_key,
)
from apps.infrastructure.storage.r2 import copy_storage_object_to_file, upload_fileobj_to_r2_storage


def batch_archive_key(batch_id, tenant_id, documents):
    revision = max(doc.pk for doc in documents)
    return f"tenants/{tenant_id}/results/wrong-notes/batches/{batch_id}-{revision}.zip"


def student_filename(doc):
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*\x7f]', "_", doc.enrollment.student.name)
    return f"{name.strip(' .')[:60] or '학생'}_{doc.enrollment_id}_오답노트.pdf"


def assemble_final_archive(doc, tenant):
    batch = doc.batch
    if batch.student_count < 2:
        return
    docs = current_documents(batch, tenant.pk)
    if len(docs) != batch.student_count or doc.pk not in {d.pk for d in docs}:
        raise ValueError("Print batch membership changed.")
    if any(d.pk != doc.pk and d.status != WrongNotePDF.Status.DONE for d in docs):
        return

    remaining = 512 * 1024 * 1024
    with tempfile.TemporaryFile() as buffer:
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
            for child in docs:
                key = wrong_note_pdf_storage_key(job=child, tenant=tenant)
                if child.pk != doc.pk and child.file_path != key:
                    raise ValueError("Unexpected student document key.")
                with archive.open(student_filename(child), "w") as member:
                    try:
                        remaining -= copy_storage_object_to_file(
                            key=key, destination=member, max_bytes=remaining,
                        )
                    except ValueError as exc:
                        raise WrongNotePDFLimitError("파일 크기를 확인하고 출력할 학생 또는 차시 범위를 줄여 주세요.") from exc
        buffer.seek(0)
        upload_fileobj_to_r2_storage(
            fileobj=buffer, key=batch_archive_key(batch.pk, tenant.pk, docs),
            content_type="application/zip", timeout_seconds=30,
        )
