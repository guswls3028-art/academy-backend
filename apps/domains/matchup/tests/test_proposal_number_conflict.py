"""Stage 6.3N (2026-05-07) — number_conflict approve guard 단위 테스트.

검증 (사용자 directive Stage 6.3N):
- _existing_problem_number_conflict helper: document 안 같은 number 검색
- approve_proposal:
  * 같은 (document_id, number) MatchupProblem 존재 시 ProposalApprovalError raise
  * MatchupProblem 미생성 (helper.create 호출 0회)
  * status=needs_review 변경
  * validation_errors 에 number_conflict 추가 (기존 보존)
  * conflicting_problem_id 기록
- 기존 차단 정책 우선순위 유지:
  * rejected → number_conflict 검사 도달 X
  * approved → number_conflict 검사 도달 X
  * manual_overlap → number_conflict 검사 도달 X
- 충돌 없는 proposal 은 정상 approve

mock 기반 + @pytest.mark.django_db (transaction.atomic 으로 인한 connection check
회피 — 실 DB 사용 안 함, ORM 호출만 mock).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.domains.matchup.proposal_helpers import (
    ProposalApprovalError,
    _existing_problem_number_conflict,
    approve_proposal,
)


# ── Helper 단위 검증 — _existing_problem_number_conflict ────────────


class TestExistingProblemNumberConflictHelper:
    """순수 mock — DB 미사용."""

    def test_no_conflict_returns_none(self):
        from apps.domains.matchup.models import MatchupProblem
        qs = MagicMock()
        qs.only = MagicMock(return_value=qs)
        qs.order_by = MagicMock(return_value=qs)
        qs.first = MagicMock(return_value=None)
        objects = MagicMock()
        objects.filter = MagicMock(return_value=qs)
        with patch.object(MatchupProblem, "objects", objects):
            result = _existing_problem_number_conflict(
                document_id=100, number=5,
            )
        assert result is None
        objects.filter.assert_called_once_with(document_id=100, number=5)

    def test_conflict_returns_id(self):
        from apps.domains.matchup.models import MatchupProblem
        existing = MagicMock(); existing.id = 12345
        qs = MagicMock()
        qs.only = MagicMock(return_value=qs)
        qs.order_by = MagicMock(return_value=qs)
        qs.first = MagicMock(return_value=existing)
        objects = MagicMock()
        objects.filter = MagicMock(return_value=qs)
        with patch.object(MatchupProblem, "objects", objects):
            result = _existing_problem_number_conflict(
                document_id=100, number=5,
            )
        assert result == 12345

    def test_only_id_field_selected(self):
        """only('id') 호출되어 다른 필드 SELECT 안 함 (read-only 안전)."""
        from apps.domains.matchup.models import MatchupProblem
        qs = MagicMock()
        qs.only = MagicMock(return_value=qs)
        qs.order_by = MagicMock(return_value=qs)
        qs.first = MagicMock(return_value=None)
        objects = MagicMock()
        objects.filter = MagicMock(return_value=qs)
        with patch.object(MatchupProblem, "objects", objects):
            _existing_problem_number_conflict(document_id=100, number=5)
        qs.only.assert_called_once_with("id")
        qs.order_by.assert_called_once_with("id")


# ── 통합 mock — approve_proposal flow + number_conflict ─────────────


def _make_user(uid=42):
    user = MagicMock()
    user.id = uid
    return user


def _make_proposal(
    *, pid=1, status="pending", validation_errors=None,
    bbox=None, document_id=100, tenant_id=1, page_number=1,
    detected_problem_number=5, engine="vlm", model_version="v1",
    image_key="",
):
    p = MagicMock()
    p.id = pid
    p.status = status
    p.validation_errors = validation_errors or []
    p.bbox = bbox or {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2, "norm": True}
    p.document_id = document_id
    p.tenant_id = tenant_id
    p.page_number = page_number
    p.detected_problem_number = detected_problem_number
    p.engine = engine
    p.model_version = model_version
    p.image_key = image_key
    p.save = MagicMock()
    return p


def _patch_proposal_get(proposal_mock):
    from apps.domains.matchup.models import ProblemSegmentationProposal
    qs = MagicMock(); qs.get = MagicMock(return_value=proposal_mock)
    objects = MagicMock(); objects.select_for_update = MagicMock(return_value=qs)
    return patch.object(ProblemSegmentationProposal, "objects", objects)


def _patch_problem_objects(create_captured: dict, conflict_id_or_none):
    """MatchupProblem.objects mock — filter().only().first() + create."""
    from apps.domains.matchup.models import MatchupProblem
    existing = None
    if conflict_id_or_none is not None:
        existing = MagicMock(); existing.id = conflict_id_or_none
    filter_qs = MagicMock()
    filter_qs.only = MagicMock(return_value=filter_qs)
    filter_qs.order_by = MagicMock(return_value=filter_qs)
    filter_qs.first = MagicMock(return_value=existing)

    def fake_create(**kwargs):
        create_captured.update(kwargs)
        m = MagicMock(); m.id = 99999
        for k, v in kwargs.items():
            setattr(m, k, v)
        return m

    objects = MagicMock()
    objects.filter = MagicMock(return_value=filter_qs)
    objects.create = MagicMock(side_effect=fake_create)
    return patch.object(MatchupProblem, "objects", objects), objects


def _patch_overlaps(returns=(False, 0.0, None)):
    return patch(
        "apps.domains.matchup.proposal_helpers.overlaps_existing_manual",
        return_value=returns,
    )


@pytest.mark.django_db
class TestApproveNumberConflictGuard:
    """Stage 6.3N — approve_proposal 안 number_conflict pre-check 검증."""

    def test_conflict_raises_and_marks_needs_review(self):
        prop = _make_proposal(status="pending", detected_problem_number=5)
        captured: dict = {}
        ppg = _patch_proposal_get(prop)
        ppc, ppc_objects = _patch_problem_objects(
            captured, conflict_id_or_none=12345,
        )
        with ppg, ppc, _patch_overlaps():
            with pytest.raises(ProposalApprovalError) as exc:
                approve_proposal(prop.id, _make_user())
        assert "number_conflict" in str(exc.value)
        # MatchupProblem.objects.create 호출 0회
        assert ppc_objects.create.call_count == 0
        # status=needs_review 변경
        assert prop.status == "needs_review"
        # validation_errors 에 number_conflict 추가
        codes = [e.get("code") for e in prop.validation_errors if isinstance(e, dict)]
        assert "number_conflict" in codes
        # conflicting_problem_id 기록
        nc_err = next(
            e for e in prop.validation_errors
            if isinstance(e, dict) and e.get("code") == "number_conflict"
        )
        assert nc_err.get("conflicting_problem_id") == 12345
        assert nc_err.get("target_number") == 5

    def test_no_conflict_proceeds_to_create(self):
        prop = _make_proposal(status="pending", detected_problem_number=99)
        captured: dict = {}
        ppg = _patch_proposal_get(prop)
        ppc, ppc_objects = _patch_problem_objects(
            captured, conflict_id_or_none=None,
        )
        with ppg, ppc, _patch_overlaps():
            new_problem = approve_proposal(prop.id, _make_user())
        assert new_problem.id == 99999
        assert ppc_objects.create.call_count == 1
        assert prop.status == "approved"
        # number_conflict 미추가
        codes = [e.get("code") for e in prop.validation_errors if isinstance(e, dict)]
        assert "number_conflict" not in codes

    def test_conflict_preserves_existing_validation_errors(self):
        existing_errors = [
            {"code": "warning_x", "detail": "prior"},
            {"code": "info_y", "detail": "more prior"},
        ]
        prop = _make_proposal(
            status="pending", detected_problem_number=5,
            validation_errors=list(existing_errors),
        )
        captured: dict = {}
        ppg = _patch_proposal_get(prop)
        ppc, _ = _patch_problem_objects(captured, conflict_id_or_none=999)
        with ppg, ppc, _patch_overlaps():
            with pytest.raises(ProposalApprovalError):
                approve_proposal(prop.id, _make_user())
        # 기존 errors 보존 + number_conflict 추가
        codes = [e.get("code") for e in prop.validation_errors if isinstance(e, dict)]
        assert "warning_x" in codes
        assert "info_y" in codes
        assert "number_conflict" in codes
        assert len(prop.validation_errors) == 3

    def test_rejected_status_blocks_before_number_check(self):
        """rejected → number_conflict 검사 도달 X (status 차단 우선)."""
        prop = _make_proposal(status="rejected", detected_problem_number=5)
        captured: dict = {}
        ppg = _patch_proposal_get(prop)
        ppc, ppc_objects = _patch_problem_objects(
            captured, conflict_id_or_none=12345,
        )
        with ppg, ppc, _patch_overlaps():
            with pytest.raises(ProposalApprovalError) as exc:
                approve_proposal(prop.id, _make_user())
        assert "rejected" in str(exc.value).lower()
        # number_conflict filter 호출 X
        assert ppc_objects.filter.call_count == 0
        assert ppc_objects.create.call_count == 0
        # validation_errors 미변경
        assert prop.validation_errors == []

    def test_already_approved_blocks_before_number_check(self):
        prop = _make_proposal(status="approved", detected_problem_number=5)
        captured: dict = {}
        ppg = _patch_proposal_get(prop)
        ppc, ppc_objects = _patch_problem_objects(
            captured, conflict_id_or_none=12345,
        )
        with ppg, ppc, _patch_overlaps():
            with pytest.raises(ProposalApprovalError) as exc:
                approve_proposal(prop.id, _make_user())
        assert "already approved" in str(exc.value).lower()
        assert ppc_objects.filter.call_count == 0
        assert ppc_objects.create.call_count == 0

    def test_manual_overlap_blocks_before_number_check(self):
        """validation_errors 의 manual_overlap → number_conflict 검사 도달 X."""
        prop = _make_proposal(
            status="pending", detected_problem_number=5,
            validation_errors=[{"code": "manual_overlap", "bbox_iou": 0.5}],
        )
        captured: dict = {}
        ppg = _patch_proposal_get(prop)
        ppc, ppc_objects = _patch_problem_objects(
            captured, conflict_id_or_none=12345,
        )
        with ppg, ppc, _patch_overlaps():
            with pytest.raises(ProposalApprovalError) as exc:
                approve_proposal(prop.id, _make_user())
        assert "manual_overlap" in str(exc.value).lower()
        assert ppc_objects.filter.call_count == 0
        assert ppc_objects.create.call_count == 0

    def test_conflict_save_called_with_status_and_validation_errors(self):
        prop = _make_proposal(status="pending", detected_problem_number=5)
        captured: dict = {}
        ppg = _patch_proposal_get(prop)
        ppc, _ = _patch_problem_objects(captured, conflict_id_or_none=12345)
        with ppg, ppc, _patch_overlaps():
            with pytest.raises(ProposalApprovalError):
                approve_proposal(prop.id, _make_user())
        # save 호출 시 update_fields 에 status / validation_errors 포함
        save_calls = prop.save.call_args_list
        assert len(save_calls) >= 1
        last = save_calls[-1]
        update_fields = last.kwargs.get("update_fields", [])
        assert "status" in update_fields
        assert "validation_errors" in update_fields

    def test_conflict_filter_uses_target_document_and_number(self):
        prop = _make_proposal(
            status="pending", document_id=735, detected_problem_number=42,
        )
        captured: dict = {}
        ppg = _patch_proposal_get(prop)
        ppc, ppc_objects = _patch_problem_objects(
            captured, conflict_id_or_none=12345,
        )
        with ppg, ppc, _patch_overlaps():
            with pytest.raises(ProposalApprovalError):
                approve_proposal(prop.id, _make_user())
        # filter 호출 인자 검증 (document_id=735, number=42)
        ppc_objects.filter.assert_called_with(document_id=735, number=42)


# ── Stage 6.3N sanity check (사용자 directive) ──────────────────


class TestStatusChoicesEnumSanity:
    """STATUS_CHOICES enum 에 needs_review 공식 등재 + serializer/API 호환 검증."""

    def test_needs_review_in_status_choices(self):
        """models.py STATUS_CHOICES 에 needs_review 등재 — drift 검출."""
        from apps.domains.matchup.models import ProblemSegmentationProposal
        valid = dict(ProblemSegmentationProposal.STATUS_CHOICES)
        assert "needs_review" in valid
        # 사용자 directive: '검수 필수' 한글 라벨
        assert valid["needs_review"] == "검수 필수"

    def test_all_required_statuses_present(self):
        from apps.domains.matchup.models import ProblemSegmentationProposal
        valid = set(dict(ProblemSegmentationProposal.STATUS_CHOICES).keys())
        # Stage 6.3N 가 사용하는 status (needs_review) +
        # 기존 차단 우선순위에 등장하는 status 모두
        for required in ("pending", "needs_review", "rejected", "approved", "auto_passed"):
            assert required in valid, f"{required} 누락"

    def test_validator_mirror_includes_needs_review(self):
        """proposal_payload_validator 의 STATUS_CHOICES mirror 와 운영 정의 일치."""
        from apps.domains.matchup.models import ProblemSegmentationProposal
        from academy.application.use_cases.ai.segmentation.proposal_payload_validator import (
            STATUS_CHOICES as MIRROR,
        )
        operating = set(dict(ProblemSegmentationProposal.STATUS_CHOICES).keys())
        assert MIRROR == operating, (
            f"validator mirror {MIRROR} != models {operating} — drift 발생"
        )


class TestApproveQueryEndpointStatusFilter:
    """views_proposal List endpoint 의 status filter 가 needs_review 통과시키는지."""

    def test_views_proposal_uses_status_choices_dict_for_validation(self):
        """views_proposal.py:101-104 의 status filter 검증 path 가
        STATUS_CHOICES 직접 사용 — drift 0 보장."""
        import inspect
        from apps.domains.matchup import views_proposal
        src = inspect.getsource(views_proposal)
        # STATUS_CHOICES dict 직접 사용 — hard-coded list 없음
        assert "STATUS_CHOICES" in src
        assert "ProblemSegmentationProposal.STATUS_CHOICES" in src
        # docstring 에 needs_review 명시
        assert "needs_review" in src

    def test_serialize_proposal_includes_status_field(self):
        """_serialize_proposal 가 status 필드 직접 노출 (drift 0)."""
        import inspect
        from apps.domains.matchup import views_proposal
        src = inspect.getsource(views_proposal._serialize_proposal)
        # status 필드 직접 — needs_review 도 자동 노출
        assert '"status": p.status' in src or "'status': p.status" in src


@pytest.mark.django_db
class TestNumberConflictRetryAfterFirstBlock:
    """number_conflict 차단 후 status=needs_review 상태에서 재approve 시도 →
    여전히 차단되는지 검증. _APPROVABLE_STATUSES 가 needs_review 포함하지만
    pre-check 가 다시 conflict 발견."""

    def test_retry_after_conflict_blocked_again(self):
        # 1차: status=pending → number_conflict 차단 → status=needs_review
        prop = _make_proposal(status="pending", detected_problem_number=5)
        captured1: dict = {}
        ppg = _patch_proposal_get(prop)
        ppc, ppc_objects = _patch_problem_objects(
            captured1, conflict_id_or_none=12345,
        )
        with ppg, ppc, _patch_overlaps():
            with pytest.raises(ProposalApprovalError):
                approve_proposal(prop.id, _make_user())
        assert prop.status == "needs_review"

        # 2차 (재시도): proposal.status=needs_review (approvable) 이지만
        # MatchupProblem.number=5 가 여전히 존재 → number_conflict 다시 detect → 차단
        # mock proposal save 가 status 만 바꿨으므로 그대로 사용
        captured2: dict = {}
        ppc2, ppc_objects2 = _patch_problem_objects(
            captured2, conflict_id_or_none=12345,
        )
        with ppg, ppc2, _patch_overlaps():
            with pytest.raises(ProposalApprovalError) as exc:
                approve_proposal(prop.id, _make_user())
        # 또 number_conflict — MatchupProblem 미생성
        assert "number_conflict" in str(exc.value)
        assert ppc_objects2.create.call_count == 0
        # validation_errors 에 number_conflict 두 번째 추가 (기존 보존)
        nc_count = sum(
            1 for e in prop.validation_errors
            if isinstance(e, dict) and e.get("code") == "number_conflict"
        )
        assert nc_count == 2  # 두 번 시도 → 두 번 누적 (기존 보존 + 추가)

    def test_retry_after_conflict_resolved_proceeds(self):
        """1차: 충돌 → needs_review. 2차: 충돌 사라진 상태 (other approve / delete /
        adjustments.problem_number 등 미래 path) → approve 성공."""
        prop = _make_proposal(status="pending", detected_problem_number=5)

        # 1차: 충돌 → needs_review
        captured1: dict = {}
        ppg = _patch_proposal_get(prop)
        ppc, _ = _patch_problem_objects(captured1, conflict_id_or_none=12345)
        with ppg, ppc, _patch_overlaps():
            with pytest.raises(ProposalApprovalError):
                approve_proposal(prop.id, _make_user())
        assert prop.status == "needs_review"

        # 2차: 충돌 해결됨 (예: 기존 MatchupProblem 삭제 또는 다른 번호로 변경)
        # status=needs_review 는 _APPROVABLE_STATUSES 안 → 통과
        captured2: dict = {}
        ppc2, ppc_objects2 = _patch_problem_objects(
            captured2, conflict_id_or_none=None,    # 충돌 해결
        )
        with ppg, ppc2, _patch_overlaps():
            new_problem = approve_proposal(prop.id, _make_user())
        assert new_problem.id == 99999
        assert ppc_objects2.create.call_count == 1
        assert prop.status == "approved"
