"""Persist one print request and dispatch one uniquely identified document at a time."""

import hashlib
import json
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from apps.domains.results.models import WrongNoteBatch, WrongNotePDF
from apps.domains.results.services.range_wrong_note_service import RangeSelectionError
from apps.domains.results.services.wrong_note_service import build_wrong_note_source_fingerprint
from apps.support.results.wrong_note_pdf_dependencies import (
    create_wrong_note_pdf_ai_job, get_wrong_note_pdf_ai_job_model,
    mark_wrong_note_pdf_ai_job_failed, publish_wrong_note_pdf_ai_job,
)

logger = logging.getLogger(__name__)
Status = WrongNotePDF.Status
STOPPED = "생성이 중단되었습니다. 다시 시도하면 완료된 학생의 파일은 그대로 사용합니다."


def create_batch(*, scope, lecture_id, tenant_id, first, last, students):
    prepared = {}
    for student in students:
        enrollment_id = student["enrollment_id"]
        if enrollment_id in prepared:
            raise RangeSelectionError("같은 학생이 두 번 선택되었습니다.")
        sources = sorted(student["sources"], key=lambda value: (value["type"], value["id"]))
        items = scope.select(enrollment_id, sources)
        prepared[enrollment_id] = {
            "enrollment_id": enrollment_id, "lecture_id": lecture_id,
            "from_session_order": first, "to_session_order": last,
            "source_selection": [
                {"type": source["type"], "id": source["id"], "numbers": sorted(source["numbers"])}
                for source in sources
            ],
            "source_fingerprint": build_wrong_note_source_fingerprint(total=len(items), items=items),
        }
    data = [prepared[k] for k in sorted(prepared)]
    names = [scope.students[k].student.name for k in sorted(prepared)]
    key = hashlib.sha256(json.dumps([tenant_id, names, data], sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        batch, created = WrongNoteBatch.objects.get_or_create(
            request_key=key, defaults={"lecture_id": lecture_id, "student_count": len(data)},
        )
        if created:
            WrongNotePDF.objects.bulk_create([WrongNotePDF(batch=batch, **row) for row in data])
        transaction.on_commit(lambda: advance_batch(batch.pk, tenant_id), robust=True)
    return batch


def current_documents(batch, tenant_id):
    latest = {}
    for doc in batch.documents.filter(
        enrollment__tenant_id=tenant_id, enrollment__student__tenant_id=tenant_id,
        enrollment__lecture_id=batch.lecture_id, lecture_id=batch.lecture_id,
    ).select_related("enrollment__student").order_by("pk"):
        latest[doc.enrollment_id] = doc
    return list(latest.values())


def _ai_jobs(documents, tenant_id):
    ids = [f"wrong-note-pdf-{doc.pk}" for doc in documents if doc.status in (Status.PENDING, Status.RUNNING)]
    records = get_wrong_note_pdf_ai_job_model().objects.filter(
        tenant_id=str(tenant_id), job_id__in=ids, source_domain="results_wrong_note_pdf",
    )
    return {int(record.source_id): record for record in records}


def advance_batch(batch_id, tenant_id):
    with transaction.atomic():
        batch = WrongNoteBatch.objects.select_for_update().filter(
            pk=batch_id, lecture__tenant_id=tenant_id,
        ).first()
        if not batch:
            return
        docs = current_documents(batch, tenant_id)
        if len(docs) != batch.student_count or _ai_jobs(docs, tenant_id):
            return
        doc = next((d for d in docs if d.status == Status.PENDING), None)
        if not doc:
            return
        ai_job = create_wrong_note_pdf_ai_job(
            pdf_job_id=doc.pk, tenant_id=tenant_id, source_fingerprint=doc.source_fingerprint,
        )
    try:
        sent = publish_wrong_note_pdf_ai_job(ai_job)
    except Exception:
        sent = False
        logger.exception("Wrong-note batch publish failed batch=%s document=%s", batch_id, doc.pk)
    if not sent:
        message = "출력 작업을 등록하지 못했습니다. 다시 시도해 주세요."
        with transaction.atomic():
            if WrongNotePDF.objects.filter(pk=doc.pk, status=Status.PENDING).update(
                status=Status.FAILED, error_message=message, updated_at=timezone.now(),
            ):
                mark_wrong_note_pdf_ai_job_failed(ai_job_id=ai_job.pk, error_message=message)
        logger.error("Wrong-note batch dispatch interrupted batch=%s document=%s", batch_id, doc.pk)


def _stopped(ai_job):
    if ai_job is None:
        return False
    if ai_job.status in {"FAILED", "REJECTED_BAD_INPUT", "FALLBACK_TO_GPU"}:
        return True
    last_seen = max(t for t in (ai_job.updated_at, ai_job.last_heartbeat_at, ai_job.lease_expires_at) if t)
    return last_seen < timezone.now() - timedelta(hours=1)


def batch_status(batch, tenant_id):
    docs = current_documents(batch, tenant_id)
    jobs = _ai_jobs(docs, tenant_id)
    students = []
    for doc in docs:
        stopped = _stopped(jobs.get(doc.pk))
        students.append({
            "enrollment_id": doc.enrollment_id, "name": doc.enrollment.student.name,
            "status": Status.FAILED if stopped else doc.status,
            "error_message": STOPPED if stopped else doc.error_message,
        })
    states = {row["status"] for row in students}
    message = ""
    if len(docs) != batch.student_count:
        state, message = Status.FAILED, "수강 정보가 변경되었습니다. 학생을 다시 선택해 주세요."
    elif states == {Status.DONE}:
        state = Status.DONE
    elif any(not _stopped(job) for job in jobs.values()):
        state = Status.RUNNING
    elif Status.FAILED in states:
        state = Status.FAILED
    elif docs and max(d.updated_at for d in docs) < timezone.now() - timedelta(minutes=2):
        state, message = Status.FAILED, STOPPED
    else:
        state = Status.PENDING
    return {"id": batch.pk, "status": state, "students": students, "detail": message}


def retry_batch(batch, tenant_id):
    with transaction.atomic():
        locked = WrongNoteBatch.objects.select_for_update().get(pk=batch.pk, lecture__tenant_id=tenant_id)
        docs = current_documents(locked, tenant_id)
        if len(docs) != batch.student_count:
            raise RangeSelectionError("수강 정보가 변경되었습니다. 학생을 다시 선택해 주세요.")
        jobs = _ai_jobs(docs, tenant_id)
        for doc in docs:
            if doc.status != Status.FAILED:
                if not _stopped(jobs.get(doc.pk)):
                    continue
                # Preserve a late successful callback that won the race.
                if not WrongNotePDF.objects.filter(pk=doc.pk, status__in=(Status.PENDING, Status.RUNNING)).update(
                    status=Status.FAILED, error_message=STOPPED, updated_at=timezone.now(),
                ):
                    continue
            WrongNotePDF.objects.create(
                batch=batch, enrollment_id=doc.enrollment_id, lecture_id=batch.lecture_id,
                from_session_order=doc.from_session_order, to_session_order=doc.to_session_order,
                source_selection=doc.source_selection, source_fingerprint=doc.source_fingerprint,
            )
        transaction.on_commit(lambda: advance_batch(batch.pk, tenant_id), robust=True)
