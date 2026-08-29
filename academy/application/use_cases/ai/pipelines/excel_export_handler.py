# PATH: apps/worker/ai_worker/ai/pipelines/excel_export_handler.py
# 엑셀 내보내기(출석/직원) — 워커에서 DB 조회 → openpyxl 생성 → R2 업로드 → download_url 반환

from __future__ import annotations

import hashlib
import logging
from io import BytesIO

from apps.shared.contracts.ai_result import AIResult
from apps.shared.contracts.ai_job import AIJob

logger = logging.getLogger(__name__)

EXCEL_EXPORT_EXPIRES_IN = 3600  # 1시간
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _upload_and_presign(
    *,
    job_id: str,
    tenant_id: str,
    fileobj: BytesIO,
    filename: str,
) -> str:
    """R2 엑셀 버킷에 업로드 후 presigned GET URL 반환."""
    from apps.infrastructure.storage.r2 import (
        upload_fileobj_to_r2_excel,
        generate_presigned_get_url_excel,
    )

    key = f"exports/{tenant_id}/{job_id}_{filename}"
    upload_fileobj_to_r2_excel(
        fileobj=fileobj,
        key=key,
        content_type=XLSX_CONTENT_TYPE,
    )
    return generate_presigned_get_url_excel(
        key=key,
        expires_in=EXCEL_EXPORT_EXPIRES_IN,
        filename=filename,
        content_type=XLSX_CONTENT_TYPE,
    )


def handle_attendance_excel_export(job: AIJob) -> AIResult:
    """출석 엑셀 내보내기: lecture_id → DB 조회 → openpyxl → R2 → download_url."""
    payload = job.payload or {}
    lecture_id = payload.get("lecture_id")
    tenant_id = str(payload.get("tenant_id") or job.tenant_id or "")

    if not lecture_id or not tenant_id:
        return AIResult.failed(job.id, "payload.lecture_id and tenant_id required")

    try:
        from academy.adapters.db.django.repositories_enrollment import get_lecture_by_id_and_tenant_id
        from apps.domains.attendance.utils.excel import build_attendance_excel

        lecture = get_lecture_by_id_and_tenant_id(lecture_id, tenant_id)
        if not lecture:
            return AIResult.failed(job.id, "lecture not found")

        workbook, filename = build_attendance_excel(lecture)
        buffer = BytesIO()
        workbook.save(buffer)
        buffer.seek(0)

        download_url = _upload_and_presign(
            job_id=job.id,
            tenant_id=tenant_id,
            fileobj=buffer,
            filename=filename,
        )
        logger.info(
            "ATTENDANCE_EXCEL_EXPORT done job_id=%s tenant_id=%s lecture_id=%s",
            job.id,
            tenant_id,
            lecture_id,
        )
        return AIResult.done(
            job.id,
            {"download_url": download_url, "filename": filename},
        )
    except Exception as e:
        logger.exception(
            "ATTENDANCE_EXCEL_EXPORT failed job_id=%s tenant_id=%s lecture_id=%s: %s",
            job.id,
            tenant_id,
            lecture_id,
            e,
        )
        return AIResult.failed(job.id, str(e)[:2000])


def handle_staff_excel_export(job: AIJob) -> AIResult:
    """직원 급여 엑셀 내보내기: year, month → DB 조회 → openpyxl → R2 → download_url."""
    payload = job.payload or {}
    year = payload.get("year")
    month = payload.get("month")
    snapshot_ids = payload.get("snapshot_ids")
    expected_revision = payload.get("revision")
    tenant_id = str(payload.get("tenant_id") or job.tenant_id or "")

    if (
        not year
        or not month
        or not tenant_id
        or not isinstance(snapshot_ids, list)
        or not snapshot_ids
        or not expected_revision
    ):
        return AIResult.failed(
            job.id,
            "payload.year, month, tenant_id, snapshot_ids and revision required",
        )

    try:
        from academy.adapters.db.django.repositories_staffs import get_payroll_snapshots_for_excel

        normalized_ids = sorted({int(snapshot_id) for snapshot_id in snapshot_ids})
        revision_source = ",".join(str(snapshot_id) for snapshot_id in normalized_ids)
        actual_revision = hashlib.sha256(
            revision_source.encode("utf-8")
        ).hexdigest()[:16]
        if actual_revision != expected_revision:
            return AIResult.failed(job.id, "snapshot revision mismatch")

        qs = list(
            get_payroll_snapshots_for_excel(
                tenant_id,
                year,
                month,
                normalized_ids,
            )
        )
        found_ids = {snapshot.id for snapshot in qs}
        if found_ids != set(normalized_ids):
            return AIResult.failed(
                job.id,
                "one or more fixed payroll snapshots are unavailable",
            )

        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment

        wb = Workbook()
        ws = wb.active
        ws.title = f"{year}-{month} 급여정산"

        headers = [
            "직원명", "연도", "월", "근무시간",
            "근무기록 금액", "승인 선결제 환급", "정산 합계(공제 전)", "확정자", "확정일시",
        ]
        ws.append(headers)

        for c in ws[1]:
            c.font = Font(bold=True)
            c.alignment = Alignment(horizontal="center")

        for s in qs:
            ws.append([
                s.staff_name or s.staff.name,
                s.year,
                s.month,
                float(s.work_hours),
                s.work_amount,
                s.approved_expense_amount,
                s.total_amount,
                getattr(s.generated_by, "username", "") if s.generated_by else "",
                s.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            ])

        filename = f"payroll_{year}_{month}.xlsx"
        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)

        download_url = _upload_and_presign(
            job_id=job.id,
            tenant_id=tenant_id,
            fileobj=buffer,
            filename=filename,
        )
        logger.info(
            "STAFF_EXCEL_EXPORT done job_id=%s tenant_id=%s year=%s month=%s",
            job.id,
            tenant_id,
            year,
            month,
        )
        return AIResult.done(
            job.id,
            {"download_url": download_url, "filename": filename},
        )
    except Exception as e:
        logger.exception(
            "STAFF_EXCEL_EXPORT failed job_id=%s tenant_id=%s: %s",
            job.id,
            tenant_id,
            e,
        )
        return AIResult.failed(job.id, str(e)[:2000])
