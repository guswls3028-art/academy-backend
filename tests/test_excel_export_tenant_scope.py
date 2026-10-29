"""Export workers must use the committed job's tenant before reading any data."""
from datetime import datetime
import hashlib
from types import SimpleNamespace
from unittest.mock import patch

from openpyxl import Workbook
import pytest

from academy.application.use_cases.ai.pipelines import excel_export_handler as handler


def export_job(kind, tenant_id="11", payload_tenant="11", format_version=1):
    payload = {"lecture_id": 123} if kind == "attendance" else {
        "year": 2026, "month": 10, "snapshot_ids": [7],
        "revision": hashlib.sha256(("deduction-v2:7" if format_version == 2 else "7").encode()).hexdigest()[:16],
    }
    if format_version == 2:
        payload["format_version"] = 2
    if payload_tenant is not None:
        payload["tenant_id"] = payload_tenant
    return SimpleNamespace(id=f"synthetic-{kind}-scope", tenant_id=tenant_id, payload=payload)


@pytest.mark.parametrize("kind", ["attendance", "staff"])
@pytest.mark.parametrize("tenant_id,payload_tenant", [
    ("11", "22"), (None, "22"), ("", "22"), ("0", "0"),
    ("-1", "-1"), ("11/../22", "11/../22"), ("١١", "١١"),
    (True, True), ("11", ""), ("11", 0),
])
def test_invalid_scope_never_queries_or_uploads(kind, tenant_id, payload_tenant):
    job = export_job(kind, tenant_id, payload_tenant)
    with (
        patch("academy.adapters.db.django.repositories_enrollment.get_lecture_by_id_and_tenant_id", return_value=None) as lecture,
        patch("academy.adapters.db.django.repositories_staffs.get_payroll_snapshots_for_excel", return_value=[]) as payroll,
        patch.object(handler, "_upload_and_presign") as upload,
    ):
        result = getattr(handler, f"handle_{kind}_excel_export")(job)
        assert result.status == "FAILED"
        lecture.assert_not_called()
        payroll.assert_not_called()
        upload.assert_not_called()


@pytest.mark.parametrize("kind", ["attendance", "staff"])
@pytest.mark.parametrize("payload", [["tenant_id", "11"], "not-a-mapping", 12])
def test_malformed_payload_is_a_failed_job_without_side_effects(kind, payload):
    job = export_job(kind)
    job.payload = payload
    with patch.object(handler, "_upload_and_presign") as upload:
        assert getattr(handler, f"handle_{kind}_excel_export")(job).status == "FAILED"
        upload.assert_not_called()


@pytest.mark.parametrize("payload_tenant", [None, "11", 11])
def test_valid_attendance_export_uses_job_tenant_with_optional_legacy_payload(payload_tenant):
    job = export_job("attendance", payload_tenant=payload_tenant)
    lecture = object()
    with (
        patch("academy.adapters.db.django.repositories_enrollment.get_lecture_by_id_and_tenant_id", return_value=lecture) as lookup,
        patch("apps.domains.attendance.utils.excel.build_attendance_excel", return_value=(Workbook(), "attendance.xlsx")),
        patch.object(handler, "_upload_and_presign", return_value="https://example.invalid/attendance.xlsx") as upload,
    ):
        assert handler.handle_attendance_excel_export(job).status == "DONE"
        lookup.assert_called_once_with(123, "11")
        assert upload.call_args.kwargs["tenant_id"] == "11"


@pytest.mark.parametrize("payload_tenant", [None, "11", 11])
@pytest.mark.parametrize("format_version", [1, 2])
def test_valid_payroll_export_preserves_both_queued_formats(payload_tenant, format_version):
    job = export_job("staff", payload_tenant=payload_tenant, format_version=format_version)
    snapshot = SimpleNamespace(
        id=7, staff_name="Synthetic employee", year=2026, month=10, work_hours=1,
        work_amount=100, approved_expense_amount=0, total_amount=100, generated_by=None,
        created_at=datetime(2026, 10, 1),
        default_deduction={"deduction_total": 3, "net_work_amount": 97, "transfer_amount": 97},
    )
    with (
        patch("academy.adapters.db.django.repositories_staffs.get_payroll_snapshots_for_excel", return_value=[snapshot]) as lookup,
        patch.object(handler, "_upload_and_presign", return_value="https://example.invalid/payroll.xlsx") as upload,
    ):
        assert handler.handle_staff_excel_export(job).status == "DONE"
        lookup.assert_called_once_with("11", 2026, 10, [7])
        assert upload.call_args.kwargs["tenant_id"] == "11"
