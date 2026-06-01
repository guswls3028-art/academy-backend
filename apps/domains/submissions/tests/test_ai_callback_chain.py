# apps/domains/submissions/tests/test_ai_callback_chain.py
"""
AI 결과 → Submission 상태 전이 체인 테스트.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

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
_GRADE_GATE_MOCK = (
    "academy.application.use_cases.omr.grading_readiness."
    "grade_omr_submission_if_ready"
)
_AI_JOB_MODEL_MOCK = "apps.domains.ai.models.AIJobModel"


def _decision(*, graded: bool, missing=None, status="answers_ready", reason=""):
    return SimpleNamespace(
        graded=graded,
        status=status,
        reason=reason,
        readiness=SimpleNamespace(missing=list(missing or [])),
    )


class TestHandleSubmission:
    @pytest.fixture(autouse=True)
    def _mock_ai_job_model(self):
        with patch(_AI_JOB_MODEL_MOCK) as m_job:
            m_job.objects.filter.return_value.first.return_value = None
            yield

    @patch(_GRADE_GATE_MOCK)
    @patch(f"{_MOD}.apply_ai_result")
    def test_done_calls_apply_with_correct_payload(self, m_apply, m_gate):
        """DONE 결과 → apply_ai_result 호출, submission_id/status 포함 확인."""
        m_apply.return_value = 42
        m_gate.return_value = _decision(graded=True, status="done", reason="graded")

        _handle_submission_ai_result(
            job_id="j1", submission_id=42, status="DONE",
            result_payload={"answers": []}, error=None, tier="basic",
        )

        m_apply.assert_called_once()
        m_gate.assert_called_once_with(42, actor="ai_callback.j1")
        p = m_apply.call_args[0][0]
        assert p["submission_id"] == 42
        assert p["status"] == "DONE"

    @patch(_GRADE_GATE_MOCK)
    @patch(f"{_MOD}.apply_ai_result")
    def test_skips_grading_when_needs_identification(self, m_apply, m_gate):
        m_apply.return_value = 42
        m_gate.return_value = _decision(
            graded=False,
            missing=["student_match"],
            status="needs_identification",
            reason="not_ready",
        )

        _handle_submission_ai_result(
            job_id="j1", submission_id=42, status="DONE",
            result_payload={}, error=None, tier="basic",
        )

        m_gate.assert_called_once_with(42, actor="ai_callback.j1")

    @patch(_GRADE_GATE_MOCK)
    @patch(f"{_MOD}.apply_ai_result")
    def test_failed_lite_passes_through_as_failed(self, m_apply, m_gate):
        """lite/basic tier FAILED도 FAILED로 전달 (0점 결과 방지)."""
        m_apply.return_value = 42
        m_gate.return_value = _decision(
            graded=False,
            missing=["status:failed"],
            status="failed",
            reason="not_ready",
        )

        _handle_submission_ai_result(
            job_id="j1", submission_id=42, status="FAILED",
            result_payload={}, error="oom", tier="lite",
        )

        p = m_apply.call_args[0][0]
        assert p["status"] == "FAILED"
        assert p["error"] == "oom"

    @patch(_GRADE_GATE_MOCK)
    @patch(f"{_MOD}.apply_ai_result")
    def test_failed_premium_passes_through(self, m_apply, m_gate):
        m_apply.return_value = 42
        m_gate.return_value = _decision(
            graded=False,
            missing=["status:failed"],
            status="failed",
            reason="not_ready",
        )

        _handle_submission_ai_result(
            job_id="j1", submission_id=42, status="FAILED",
            result_payload={}, error="err", tier="premium",
        )

        p = m_apply.call_args[0][0]
        assert p["status"] == "FAILED"
        assert p["error"] == "err"

    @patch(_GRADE_GATE_MOCK)
    @patch(f"{_MOD}.apply_ai_result")
    def test_apply_returns_none_no_grading(self, m_apply, m_gate):
        m_apply.return_value = None

        _handle_submission_ai_result(
            job_id="j1", submission_id=42, status="DONE",
            result_payload={}, error=None, tier="basic",
        )
        m_gate.assert_not_called()

    @patch(_GRADE_GATE_MOCK)
    @patch(f"{_MOD}.apply_ai_result")
    def test_idempotent_duplicate(self, m_apply, m_gate):
        m_apply.return_value = 42
        m_gate.return_value = _decision(
            graded=False,
            status="done",
            reason="already_done",
        )

        _handle_submission_ai_result(
            job_id="j1", submission_id=42, status="DONE",
            result_payload={}, error=None, tier="basic",
        )
        _handle_submission_ai_result(
            job_id="j1", submission_id=42, status="DONE",
            result_payload={}, error=None, tier="basic",
        )

        assert m_apply.call_count == 2
        assert m_gate.call_count == 2


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
