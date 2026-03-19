# PATH: apps/domains/submissions/tests/test_transition.py
"""
Submission 상태 전이 SSOT 테스트.

전이 서비스의 허용/금지/종단/관리자오버라이드/벌크 전이를 전수 검증한다.
"""
import pytest

from apps.domains.submissions.models import Submission
from apps.domains.submissions.services.transition import (
    STATUS_FLOW,
    ADMIN_OVERRIDE_FLOW,
    TERMINAL_STATES,
    InvalidTransitionError,
    can_transit,
    transit,
    bulk_transit,
)

S = Submission.Status

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _make_submission(status: str) -> Submission:
    """DB 없이 인메모리 Submission stub 생성."""
    sub = Submission.__new__(Submission)
    sub.pk = 999
    sub.id = 999
    sub.status = status
    sub.error_message = ""
    return sub


# ──────────────────────────────────────────────
# A. 허용 전이 전수 테스트
# ──────────────────────────────────────────────

ALL_ALLOWED_TRANSITIONS = []
for from_s, to_set in STATUS_FLOW.items():
    for to_s in to_set:
        ALL_ALLOWED_TRANSITIONS.append((from_s, to_s))


@pytest.mark.parametrize("from_status,to_status", ALL_ALLOWED_TRANSITIONS)
def test_allowed_transitions(from_status, to_status):
    """STATUS_FLOW에 정의된 모든 전이가 허용되는지 확인."""
    assert can_transit(from_status, to_status) is True
    sub = _make_submission(from_status)
    transit(sub, to_status, actor="test")
    assert sub.status == to_status


# ──────────────────────────────────────────────
# B. 금지 전이 전수 테스트
# ──────────────────────────────────────────────

ALL_STATUSES = list(S)

FORBIDDEN_TRANSITIONS = []
for from_s in ALL_STATUSES:
    allowed = STATUS_FLOW.get(from_s, set())
    for to_s in ALL_STATUSES:
        if to_s not in allowed and from_s != to_s:
            FORBIDDEN_TRANSITIONS.append((from_s, to_s))


@pytest.mark.parametrize("from_status,to_status", FORBIDDEN_TRANSITIONS)
def test_forbidden_transitions(from_status, to_status):
    """STATUS_FLOW에 없는 전이는 차단되는지 확인."""
    assert can_transit(from_status, to_status) is False
    sub = _make_submission(from_status)
    with pytest.raises(InvalidTransitionError):
        transit(sub, to_status, actor="test")
    # 상태가 변경되지 않았는지 확인
    assert sub.status == from_status


# ──────────────────────────────────────────────
# C. 종단 상태 재전이 금지
# ──────────────────────────────────────────────

@pytest.mark.parametrize("terminal", list(TERMINAL_STATES))
def test_terminal_states_cannot_exit(terminal):
    """종단 상태에서 다른 상태로의 전이가 불가능한지 확인 (STATUS_FLOW에 정의된 것 제외)."""
    allowed_from_terminal = STATUS_FLOW.get(terminal, set())
    sub = _make_submission(terminal)
    for to_s in ALL_STATUSES:
        if to_s in allowed_from_terminal:
            continue  # DONE→SUPERSEDED는 허용
        if to_s == terminal:
            continue  # 자기 자신은 skip
        with pytest.raises(InvalidTransitionError):
            transit(sub, to_s, actor="test")


def test_done_to_superseded_is_allowed():
    """DONE → SUPERSEDED 전이가 허용되는지 확인."""
    sub = _make_submission(S.DONE)
    transit(sub, S.SUPERSEDED, actor="test")
    assert sub.status == S.SUPERSEDED


def test_superseded_is_fully_terminal():
    """SUPERSEDED에서는 어떤 전이도 불가능."""
    sub = _make_submission(S.SUPERSEDED)
    for to_s in ALL_STATUSES:
        if to_s == S.SUPERSEDED:
            continue
        with pytest.raises(InvalidTransitionError):
            transit(sub, to_s, actor="test")


# ──────────────────────────────────────────────
# D. Admin Override 전이 테스트
# ──────────────────────────────────────────────

ADMIN_OVERRIDE_TRANSITIONS = []
for from_s, to_set in ADMIN_OVERRIDE_FLOW.items():
    for to_s in to_set:
        ADMIN_OVERRIDE_TRANSITIONS.append((from_s, to_s))


@pytest.mark.parametrize("from_status,to_status", ADMIN_OVERRIDE_TRANSITIONS)
def test_admin_override_allowed(from_status, to_status):
    """관리자 오버라이드 전이가 admin_override=True일 때 허용되는지 확인."""
    assert can_transit(from_status, to_status, admin_override=True) is True
    sub = _make_submission(from_status)
    transit(sub, to_status, admin_override=True, actor="test_admin")
    assert sub.status == to_status


@pytest.mark.parametrize("from_status,to_status", ADMIN_OVERRIDE_TRANSITIONS)
def test_admin_override_blocked_without_flag(from_status, to_status):
    """관리자 오버라이드 전이가 admin_override=False일 때 차단되는지 확인 (STATUS_FLOW에도 없는 경우)."""
    if can_transit(from_status, to_status):
        pytest.skip("Already in STATUS_FLOW — not a pure admin override")
    assert can_transit(from_status, to_status, admin_override=False) is False


def test_grading_blocks_admin_override():
    """GRADING 상태에서는 admin_override도 차단."""
    sub = _make_submission(S.GRADING)
    with pytest.raises(InvalidTransitionError):
        transit(sub, S.ANSWERS_READY, admin_override=True, actor="test_admin")


def test_superseded_blocks_admin_override():
    """SUPERSEDED 상태에서는 admin_override=True여도 ANSWERS_READY 불허."""
    sub = _make_submission(S.SUPERSEDED)
    assert can_transit(S.SUPERSEDED, S.ANSWERS_READY, admin_override=True) is False
    with pytest.raises(InvalidTransitionError):
        transit(sub, S.ANSWERS_READY, admin_override=True, actor="test_admin")


# ──────────────────────────────────────────────
# E. 구체적 파이프라인 시나리오
# ──────────────────────────────────────────────

def test_online_pipeline():
    """ONLINE 제출 파이프라인: SUBMITTED → ANSWERS_READY → GRADING → DONE."""
    sub = _make_submission(S.SUBMITTED)
    # dispatcher 호출 시뮬레이션 (online은 SUBMITTED→GRADING shortcut이지만,
    # SubmissionService.process()가 SUBMITTED→ANSWERS_READY를 먼저 함)

    # Step 1: SubmissionService.process
    transit(sub, S.ANSWERS_READY, actor="test")
    assert sub.status == S.ANSWERS_READY

    # Step 2: dispatcher sets GRADING
    transit(sub, S.GRADING, actor="test")
    assert sub.status == S.GRADING

    # Step 3: grading done
    transit(sub, S.DONE, actor="test")
    assert sub.status == S.DONE


def test_file_pipeline():
    """FILE 제출 파이프라인: SUBMITTED → DISPATCHED → ANSWERS_READY → GRADING → DONE."""
    sub = _make_submission(S.SUBMITTED)

    transit(sub, S.DISPATCHED, actor="test")
    transit(sub, S.ANSWERS_READY, actor="test")
    transit(sub, S.GRADING, actor="test")
    transit(sub, S.DONE, actor="test")
    assert sub.status == S.DONE


def test_file_pipeline_with_identification():
    """FILE 제출 (식별 필요): SUBMITTED → DISPATCHED → NEEDS_ID → ANSWERS_READY → GRADING → DONE."""
    sub = _make_submission(S.SUBMITTED)

    transit(sub, S.DISPATCHED, actor="test")
    transit(sub, S.NEEDS_IDENTIFICATION, actor="test")
    transit(sub, S.ANSWERS_READY, actor="test")
    transit(sub, S.GRADING, actor="test")
    transit(sub, S.DONE, actor="test")
    assert sub.status == S.DONE


def test_failure_and_retry():
    """실패 후 재시도: ... → GRADING → FAILED → SUBMITTED → (다시 처리)."""
    sub = _make_submission(S.SUBMITTED)

    transit(sub, S.DISPATCHED, actor="test")
    transit(sub, S.ANSWERS_READY, actor="test")
    transit(sub, S.GRADING, actor="test")
    transit(sub, S.FAILED, actor="test")

    # retry
    transit(sub, S.SUBMITTED, actor="test")
    transit(sub, S.DISPATCHED, actor="test")
    transit(sub, S.ANSWERS_READY, actor="test")
    transit(sub, S.GRADING, actor="test")
    transit(sub, S.DONE, actor="test")
    assert sub.status == S.DONE


def test_retake_supersede():
    """재응시: DONE → SUPERSEDED."""
    sub = _make_submission(S.DONE)
    transit(sub, S.SUPERSEDED, actor="test")
    assert sub.status == S.SUPERSEDED


def test_admin_regrade_done_submission():
    """관리자 재채점: DONE → ANSWERS_READY (admin_override) → GRADING → DONE."""
    sub = _make_submission(S.DONE)

    # admin manual edit
    transit(sub, S.ANSWERS_READY, admin_override=True, actor="test_admin")

    # normal pipeline
    transit(sub, S.GRADING, actor="test")
    transit(sub, S.DONE, actor="test")
    assert sub.status == S.DONE


def test_admin_regrade_failed_submission():
    """관리자 재채점: FAILED → ANSWERS_READY (admin_override) → GRADING → DONE."""
    sub = _make_submission(S.FAILED)

    transit(sub, S.ANSWERS_READY, admin_override=True, actor="test_admin")
    transit(sub, S.GRADING, actor="test")
    transit(sub, S.DONE, actor="test")
    assert sub.status == S.DONE


# ──────────────────────────────────────────────
# F. Error message 처리
# ──────────────────────────────────────────────

def test_error_message_set_on_failure():
    """FAILED 전이 시 error_message가 설정됨."""
    sub = _make_submission(S.GRADING)
    transit(sub, S.FAILED, error_message="test error", actor="test")
    assert sub.error_message == "test error"


def test_error_message_cleared_on_non_failure():
    """FAILED가 아닌 전이 시 error_message가 초기화됨."""
    sub = _make_submission(S.FAILED)
    sub.error_message = "old error"
    transit(sub, S.SUBMITTED, actor="test")
    assert sub.error_message == ""


# ──────────────────────────────────────────────
# G. Bulk Transit
# ──────────────────────────────────────────────

def test_bulk_transit_validates_from_status():
    """bulk_transit에서 금지된 전이는 InvalidTransitionError."""
    with pytest.raises(InvalidTransitionError):
        bulk_transit(
            Submission.objects.none(),
            S.DONE,
            from_status=S.SUBMITTED,
        )


def test_bulk_transit_allows_done_to_superseded():
    """bulk_transit에서 DONE→SUPERSEDED는 허용."""
    # can_transit check만 확인 (실제 DB 없이)
    assert can_transit(S.DONE, S.SUPERSEDED) is True


# ──────────────────────────────────────────────
# H. STATUS_FLOW 완전성 검증
# ──────────────────────────────────────────────

def test_all_statuses_in_flow():
    """모든 상태가 STATUS_FLOW에 정의되어 있는지 확인 (EXTRACTING 제외 — orphan)."""
    for s in ALL_STATUSES:
        if s == S.EXTRACTING:
            continue  # orphan — 의도적 미포함
        assert s in STATUS_FLOW, f"{s} is not in STATUS_FLOW"


def test_extracting_not_in_flow():
    """EXTRACTING은 orphan 상태 — STATUS_FLOW에 없어야 함."""
    assert S.EXTRACTING not in STATUS_FLOW


def test_no_self_transitions():
    """자기 자신으로의 전이는 STATUS_FLOW에 없어야 함."""
    for from_s, to_set in STATUS_FLOW.items():
        assert from_s not in to_set, f"Self-transition found: {from_s}"
