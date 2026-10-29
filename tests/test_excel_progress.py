from unittest.mock import patch

import pytest

from academy.application.use_cases.ai.pipelines.excel_handler import _record_progress
from academy.application.use_cases.ai.pipelines.excel_handler import handle_excel_parsing_job
from academy.application.services.excel_parsing_service import ExcelValidationError
from apps.shared.contracts.ai_job import AIJob


@pytest.mark.parametrize("step,index,display", [
    ("downloading", 1, "다운로드"), ("parsing", 2, "파싱"),
    ("enrolling", 3, "등록"), ("done", 4, "완료"),
])
def test_excel_progress_reaches_tenant_scoped_cache(step, index, display):
    with patch("academy.adapters.cache.redis_progress_adapter.RedisProgressAdapter") as adapter:
        _record_progress("qa-excel-progress", step, 55, index, 30, tenant_id="41")
    adapter.return_value.record_progress.assert_called_once_with(
        "qa-excel-progress", step,
        {"percent": 55, "step_index": index, "step_total": 4,
         "step_name": step, "step_name_display": display, "step_percent": 30},
        tenant_id="41",
    )


def test_progress_cache_outage_does_not_fail_registration():
    with patch("academy.adapters.cache.redis_progress_adapter.RedisProgressAdapter", side_effect=OSError):
        _record_progress("qa-excel-progress", "parsing", 25, 2, 0, tenant_id="41")


@pytest.mark.parametrize("error,expected", [
    (ExcelValidationError("3행 학부모 전화번호를 확인해 주세요."), "3행 학부모 전화번호를 확인해 주세요."),
    (RuntimeError("internal SQL host=private password=synthetic"), "엑셀 처리 중 오류가 발생했습니다. 작업 상태를 확인한 뒤 다시 시도해 주세요."),
])
def test_worker_exposes_only_user_safe_validation_errors(error, expected):
    job = AIJob(id="qa-error", type="excel_parsing", tenant_id="41", payload={"file_key": "tenants/41/excel/qa.xlsx", "tenant_id": "41"})
    with patch("academy.application.use_cases.ai.pipelines.excel_handler.R2ObjectStorageAdapter"), \
         patch("academy.application.use_cases.ai.pipelines.excel_handler._record_progress"), \
         patch("academy.application.use_cases.ai.pipelines.excel_handler.ExcelParsingService.run", side_effect=error):
        result = handle_excel_parsing_job(job)
    assert result.status == "FAILED"
    assert result.error == expected
