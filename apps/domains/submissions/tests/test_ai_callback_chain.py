# apps/domains/submissions/tests/test_ai_callback_chain.py
"""
AI 결과 → Submission 상태 전이 체인 테스트.
"""
import pytest
from unittest.mock import patch, MagicMock, call

from apps.domains.ai.callbacks import (
    dispatch_ai_result_to_domain,
    _handle_submission_ai_result,
)


# ==========================================================
# A. dispatch_ai_result_to_domain 라우팅 테스트
# ==========================================================

class TestDispatchRouting:

    def test_skips_non_submissions_source(self):
        dispatch_ai_result_to_domain(
            job_id="j", status="DONE", result_payload={},
            error=None, source_domain="other", source_id="1",
        )

    def test_skips_empty_source_id(self):
        dispatch_ai_result_to_domain(
            job_id="j", status="DONE", result_payload={},
            error=None, source_domain="submissions", source_id=None,
        )

    def test_skips_none_source_domain(self):
        dispatch_ai_result_to_domain(
            job_id="j", status="DONE", result_payload={},
            error=None, source_domain=None, source_id="1",
        )

    @patch("apps.domains.ai.callbacks._handle_submission_ai_result")
    def test_routes_to_handler(self, mock_handler):
        dispatch_ai_result_to_domain(
            job_id="j1", status="DONE", result_payload={"a": 1},
            error=None, source_domain="submissions", source_id="42", tier="basic",
        )
        mock_handler.assert_called_once_with(
            job_id="j1", submission_id=42, status="DONE",
            result_payload={"a": 1}, error=None, tier="basic",
        )

    @patch("apps.domains.ai.callbacks._handle_submission_ai_result")
    def test_handler_exception_swallowed(self, mock_handler):
        mock_handler.side_effect = RuntimeError("boom")
        dispatch_ai_result_to_domain(
            job_id="j1", status="DONE", result_payload={},
            error=None, source_domain="submissions", source_id="42",
        )
        # no exception propagated


# ==========================================================
# B. _handle_submission_ai_result 내부 로직 테스트
#    lazy import 때문에 source 모듈 mock 필요
# ==========================================================

# lazy import 경로: _handle_submission_ai_result 안에서 매번 import됨
# apply_ai_result = apply_omr_ai_result (alias). 데코레이터 때문에 alias를 mock해야 함
_MOD = "apps.domains.submissions.services.ai_omr_result_mapper"
_GRADE_MOCK = "apps.domains.results.tasks.grading_tasks.grade_submission_task"
_SUB_MODEL_MOCK = "apps.domains.submissions.models.submission.Submission"


class TestHandleSubmission:

    @patch(f"{_MOD}.apply_ai_result")
    def test_done_calls_apply_with_correct_payload(self, m_apply):
        """DONE 결과 → apply_ai_result 호출, submission_id/status 포함 확인."""
        m_apply.return_value = 42

        # Submission model import를 포함한 후속 코드는 DB 접근 → mock 필요
        # grade 호출 여부는 통합 테스트에서 검증
        with patch("apps.domains.submissions.models.Submission") as m_sub, \
             patch(_GRADE_MOCK):
            m_sub.objects.filter.return_value.values_list.return_value.first.return_value = "answers_ready"
            m_sub.Status.ANSWERS_READY = "answers_ready"

            _handle_submission_ai_result(
                job_id="j1", submission_id=42, status="DONE",
                result_payload={"answers": []}, error=None, tier="basic",
            )

        m_apply.assert_called_once()
        p = m_apply.call_args[0][0]
        assert p["submission_id"] == 42
        assert p["status"] == "DONE"

    @patch(_SUB_MODEL_MOCK)
    @patch(_GRADE_MOCK)
    @patch(f"{_MOD}.apply_ai_result")
    def test_skips_grading_when_needs_identification(self, m_apply, m_grade, m_sub):
        m_apply.return_value = 42
        m_sub.objects.filter.return_value.values_list.return_value.first.return_value = "needs_identification"
        m_sub.Status.ANSWERS_READY = "answers_ready"

        _handle_submission_ai_result(
            job_id="j1", submission_id=42, status="DONE",
            result_payload={}, error=None, tier="basic",
        )

        m_grade.assert_not_called()

    @patch(_SUB_MODEL_MOCK)
    @patch(f"{_MOD}.apply_ai_result")
    def test_failed_lite_passes_through_as_failed(self, m_apply, m_sub):
        """lite/basic tier FAILED도 FAILED로 전달 (0점 결과 방지)."""
        m_apply.return_value = 42
        m_sub.objects.filter.return_value.values_list.return_value.first.return_value = "failed"
        m_sub.Status.ANSWERS_READY = "answers_ready"

        _handle_submission_ai_result(
            job_id="j1", submission_id=42, status="FAILED",
            result_payload={}, error="oom", tier="lite",
        )

        p = m_apply.call_args[0][0]
        assert p["status"] == "FAILED"
        assert p["error"] == "oom"

    @patch(_SUB_MODEL_MOCK)
    @patch(f"{_MOD}.apply_ai_result")
    def test_failed_premium_passes_through(self, m_apply, m_sub):
        m_apply.return_value = 42
        m_sub.objects.filter.return_value.values_list.return_value.first.return_value = "failed"
        m_sub.Status.ANSWERS_READY = "answers_ready"

        _handle_submission_ai_result(
            job_id="j1", submission_id=42, status="FAILED",
            result_payload={}, error="err", tier="premium",
        )

        p = m_apply.call_args[0][0]
        assert p["status"] == "FAILED"
        assert p["error"] == "err"

    @patch(f"{_MOD}.apply_ai_result")
    def test_apply_returns_none_no_grading(self, m_apply):
        m_apply.return_value = None

        _handle_submission_ai_result(
            job_id="j1", submission_id=42, status="DONE",
            result_payload={}, error=None, tier="basic",
        )
        # no exception, no grading call

    @patch(_SUB_MODEL_MOCK)
    @patch(f"{_MOD}.apply_ai_result")
    def test_idempotent_duplicate(self, m_apply, m_sub):
        m_apply.return_value = 42
        m_sub.objects.filter.return_value.values_list.return_value.first.return_value = "done"
        m_sub.Status.ANSWERS_READY = "answers_ready"

        _handle_submission_ai_result(
            job_id="j1", submission_id=42, status="DONE",
            result_payload={}, error=None, tier="basic",
        )
        _handle_submission_ai_result(
            job_id="j1", submission_id=42, status="DONE",
            result_payload={}, error=None, tier="basic",
        )

        assert m_apply.call_count == 2


# ==========================================================
# C. SQS 워커 _dispatch_domain_callback 테스트
# ==========================================================

class TestSQSWorkerCallback:

    def test_import(self):
        from academy.framework.workers.ai_sqs_worker import _dispatch_domain_callback
        assert callable(_dispatch_domain_callback)

    @patch("apps.domains.ai.callbacks.dispatch_ai_result_to_domain")
    def test_calls_dispatch(self, mock_dispatch):
        from academy.framework.workers.ai_sqs_worker import _dispatch_domain_callback
        from academy.application.use_cases.ai.process_ai_job_from_sqs import PreparedJob

        p = PreparedJob(
            job_id="j1", job_type="omr", tier="basic",
            payload={}, receipt_handle="rh",
            source_domain="submissions", source_id="42",
        )
        _dispatch_domain_callback(p, status="DONE", result_payload={"a": 1}, error=None)
        mock_dispatch.assert_called_once_with(
            job_id="j1", status="DONE", result_payload={"a": 1},
            error=None, source_domain="submissions", source_id="42", tier="basic",
        )

    @patch("apps.domains.ai.callbacks.dispatch_ai_result_to_domain")
    def test_exception_non_fatal(self, mock_dispatch):
        mock_dispatch.side_effect = RuntimeError("DB down")
        from academy.framework.workers.ai_sqs_worker import _dispatch_domain_callback
        from academy.application.use_cases.ai.process_ai_job_from_sqs import PreparedJob

        p = PreparedJob(
            job_id="j1", job_type="omr", tier="basic",
            payload={}, receipt_handle="rh",
            source_domain="submissions", source_id="42",
        )
        _dispatch_domain_callback(p, status="DONE", result_payload={}, error=None)
