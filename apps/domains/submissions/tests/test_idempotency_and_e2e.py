# apps/domains/submissions/tests/test_idempotency_and_e2e.py
"""
멱등성 + E2E 파이프라인 테스트.

중복/역순 이벤트, 전체 파이프라인 경로 검증.
DB 없이 transition 레이어에서 검증한다.
"""
import pytest

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.transition import (
    transit, InvalidTransitionError, STATUS_FLOW,
)

S = Submission.Status


def _sub(status: str) -> Submission:
    s = Submission.__new__(Submission)
    s.pk = 1
    s.id = 1
    s.status = status
    s.error_message = ""
    return s


# ==========================================================
# A. 멱등성 테스트 — 중복 이벤트
# ==========================================================

class TestIdempotency:

    def test_duplicate_done_from_grading(self):
        """GRADING → DONE 중복: 첫번째 성공, 두번째 InvalidTransitionError (종단)."""
        sub = _sub(S.GRADING)
        transit(sub, S.DONE, actor="test")
        assert sub.status == S.DONE
        # 두 번째: DONE은 STATUS_FLOW에서 {SUPERSEDED}만 허용
        with pytest.raises(InvalidTransitionError):
            transit(sub, S.DONE, actor="test")

    def test_duplicate_failed_from_dispatched(self):
        """DISPATCHED → FAILED 중복: 첫번째 성공, 두번째는 FAILED→FAILED 금지."""
        sub = _sub(S.DISPATCHED)
        transit(sub, S.FAILED, actor="test")
        with pytest.raises(InvalidTransitionError):
            transit(sub, S.FAILED, actor="test")

    def test_duplicate_answers_ready_from_dispatched(self):
        """DISPATCHED → ANSWERS_READY 중복: 첫번째 성공, 두번째는 ANSWERS_READY→ANSWERS_READY 금지."""
        sub = _sub(S.DISPATCHED)
        transit(sub, S.ANSWERS_READY, actor="test")
        with pytest.raises(InvalidTransitionError):
            transit(sub, S.ANSWERS_READY, actor="test")


# ==========================================================
# B. 역순 이벤트 테스트
# ==========================================================

class TestOutOfOrderEvents:

    def test_failed_then_late_done(self):
        """FAILED 후 늦은 DONE: FAILED → DONE은 금지 (FAILED→SUBMITTED만 허용)."""
        sub = _sub(S.DISPATCHED)
        transit(sub, S.FAILED, actor="test")
        assert sub.status == S.FAILED
        # 늦은 DONE 도착
        with pytest.raises(InvalidTransitionError):
            transit(sub, S.DONE, actor="test")
        assert sub.status == S.FAILED

    def test_done_then_late_failed(self):
        """DONE 후 늦은 FAILED: DONE → FAILED는 금지 (DONE→SUPERSEDED만 허용)."""
        sub = _sub(S.GRADING)
        transit(sub, S.DONE, actor="test")
        assert sub.status == S.DONE
        with pytest.raises(InvalidTransitionError):
            transit(sub, S.DONE, actor="late_duplicate")
        with pytest.raises(InvalidTransitionError):
            transit(sub, S.FAILED, actor="late_failed")
        assert sub.status == S.DONE

    def test_timeout_then_late_real_result(self):
        """타임아웃 FAILED 후 실제 결과 도착: 이미 FAILED이므로 거부."""
        sub = _sub(S.DISPATCHED)
        # 타임아웃으로 FAILED
        transit(sub, S.FAILED, error_message="timeout", actor="test")
        assert sub.status == S.FAILED
        # 실제 결과 도착 (ANSWERS_READY 시도)
        with pytest.raises(InvalidTransitionError):
            transit(sub, S.ANSWERS_READY, actor="late_result")
        assert sub.status == S.FAILED

    def test_timeout_then_retry_then_success(self):
        """타임아웃 FAILED → 재시도 → 성공 경로."""
        sub = _sub(S.DISPATCHED)
        transit(sub, S.FAILED, error_message="timeout", actor="test")
        # 재시도
        transit(sub, S.SUBMITTED, actor="admin_retry")
        transit(sub, S.DISPATCHED, actor="dispatcher")
        transit(sub, S.ANSWERS_READY, actor="ai")
        transit(sub, S.GRADING, actor="grader")
        transit(sub, S.DONE, actor="grader")
        assert sub.status == S.DONE


# ==========================================================
# C. 전체 E2E 파이프라인 경로 테스트
# ==========================================================

class TestE2EPipeline:

    def test_online_full_path(self):
        """ONLINE 제출: SUBMITTED → ANSWERS_READY → GRADING → DONE."""
        sub = _sub(S.SUBMITTED)
        transit(sub, S.ANSWERS_READY, actor="service.process")
        transit(sub, S.GRADING, actor="dispatcher")
        transit(sub, S.DONE, actor="dispatcher")
        assert sub.status == S.DONE

    def test_omr_scan_success(self):
        """OMR 스캔 성공: SUBMITTED → DISPATCHED → ANSWERS_READY → GRADING → DONE."""
        sub = _sub(S.SUBMITTED)
        transit(sub, S.DISPATCHED, actor="dispatcher")
        transit(sub, S.ANSWERS_READY, actor="ai_callback")
        transit(sub, S.GRADING, actor="grader")
        transit(sub, S.DONE, actor="grader")
        assert sub.status == S.DONE

    def test_omr_scan_success_with_extracting(self):
        """OMR 스캔 성공: SUBMITTED → DISPATCHED → EXTRACTING → ANSWERS_READY → GRADING → DONE."""
        sub = _sub(S.SUBMITTED)
        transit(sub, S.DISPATCHED, actor="dispatcher")
        transit(sub, S.EXTRACTING, actor="worker")
        transit(sub, S.ANSWERS_READY, actor="ai_callback")
        transit(sub, S.GRADING, actor="grader")
        transit(sub, S.DONE, actor="grader")
        assert sub.status == S.DONE

    def test_omr_scan_needs_identification(self):
        """OMR 식별 실패: DISPATCHED → NEEDS_ID → (수동매칭) → ANSWERS_READY → GRADING → DONE."""
        sub = _sub(S.SUBMITTED)
        transit(sub, S.DISPATCHED, actor="dispatcher")
        transit(sub, S.NEEDS_IDENTIFICATION, actor="ai_callback")
        transit(sub, S.ANSWERS_READY, actor="manual_edit")
        transit(sub, S.GRADING, actor="grader")
        transit(sub, S.DONE, actor="grader")
        assert sub.status == S.DONE

    def test_omr_scan_ai_failure(self):
        """AI 처리 실패: DISPATCHED → FAILED → (재시도) → SUBMITTED → ... → DONE."""
        sub = _sub(S.SUBMITTED)
        transit(sub, S.DISPATCHED, actor="dispatcher")
        transit(sub, S.FAILED, error_message="AI error", actor="ai_callback")
        # 재시도
        transit(sub, S.SUBMITTED, actor="admin_retry")
        transit(sub, S.DISPATCHED, actor="dispatcher")
        transit(sub, S.ANSWERS_READY, actor="ai_callback")
        transit(sub, S.GRADING, actor="grader")
        transit(sub, S.DONE, actor="grader")
        assert sub.status == S.DONE

    def test_grading_failure_and_recovery(self):
        """채점 실패 후 재채점: GRADING → FAILED → SUBMITTED → ... → DONE."""
        sub = _sub(S.SUBMITTED)
        transit(sub, S.ANSWERS_READY, actor="service")
        transit(sub, S.GRADING, actor="grader")
        transit(sub, S.FAILED, error_message="grading error", actor="grader")
        # 재시도
        transit(sub, S.SUBMITTED, actor="admin_retry")
        transit(sub, S.ANSWERS_READY, actor="service")
        transit(sub, S.GRADING, actor="grader")
        transit(sub, S.DONE, actor="grader")
        assert sub.status == S.DONE

    def test_retake_supersede(self):
        """재응시: DONE → SUPERSEDED (기존 제출), 새 SUBMITTED → ... → DONE."""
        old_sub = _sub(S.DONE)
        transit(old_sub, S.SUPERSEDED, actor="student_retake")
        assert old_sub.status == S.SUPERSEDED

        new_sub = _sub(S.SUBMITTED)
        transit(new_sub, S.ANSWERS_READY, actor="service")
        transit(new_sub, S.GRADING, actor="grader")
        transit(new_sub, S.DONE, actor="grader")
        assert new_sub.status == S.DONE

    def test_admin_manual_regrade(self):
        """관리자 재채점: DONE → ANSWERS_READY (override) → GRADING → DONE."""
        sub = _sub(S.DONE)
        transit(sub, S.ANSWERS_READY, admin_override=True, actor="admin")
        transit(sub, S.GRADING, actor="grader")
        transit(sub, S.DONE, actor="grader")
        assert sub.status == S.DONE

    def test_file_missing_failure(self):
        """파일 누락: SUBMITTED → FAILED."""
        sub = _sub(S.SUBMITTED)
        transit(sub, S.FAILED, error_message="file_key missing", actor="dispatcher")
        assert sub.status == S.FAILED


# ==========================================================
# D. apply_omr_ai_result 멱등성 가드 테스트
# ==========================================================

class TestApplyOmrIdempotency:

    def test_already_processed_statuses_constant(self):
        """_ALREADY_PROCESSED_STATUSES에 올바른 상태가 포함되어 있는지."""
        from apps.domains.submissions.services.ai_omr_result_mapper import _ALREADY_PROCESSED_STATUSES
        assert S.ANSWERS_READY in _ALREADY_PROCESSED_STATUSES
        assert S.GRADING in _ALREADY_PROCESSED_STATUSES
        assert S.DONE in _ALREADY_PROCESSED_STATUSES
        assert S.SUPERSEDED in _ALREADY_PROCESSED_STATUSES
        # DISPATCHED는 포함되면 안 됨 (처리 대상이므로)
        assert S.DISPATCHED not in _ALREADY_PROCESSED_STATUSES
        assert S.FAILED not in _ALREADY_PROCESSED_STATUSES
        assert S.NEEDS_IDENTIFICATION not in _ALREADY_PROCESSED_STATUSES


# ==========================================================
# E. callbacks.detect_stuck_dispatched 테스트
# ==========================================================

class TestDetectStuck:

    def test_import(self):
        from apps.domains.ai.callbacks import detect_stuck_dispatched
        assert callable(detect_stuck_dispatched)


# ==========================================================
# F. 전이 coverage 완전성 검증
# ==========================================================

class TestTransitionCoverage:

    def test_all_non_terminal_statuses_have_exit(self):
        """종단 아닌 모든 상태에서 최소 1개의 exit 전이가 존재."""
        for s in S:
            exits = STATUS_FLOW.get(s, set())
            if s in (S.DONE, S.SUPERSEDED):
                # DONE은 SUPERSEDED로만 전이, SUPERSEDED는 종단
                continue
            assert len(exits) > 0, f"{s} has no exit transitions"

    def test_every_non_initial_status_is_reachable(self):
        """초기 상태를 제외한 모든 상태가 다른 상태에서 도달 가능."""
        reachable = set()
        for from_s, to_set in STATUS_FLOW.items():
            reachable.update(to_set)
        for s in S:
            if s == S.SUBMITTED:
                continue  # SUBMITTED는 초기 상태
            assert s in reachable, f"{s} is not reachable from any other status"
