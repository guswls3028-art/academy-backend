"""Stage 3 (2026-05-06): ProblemSegmentationProposal 운영 격리 단위 테스트.

원칙 (사용자 directive):
- Proposal 생성이 selected_problem_ids를 바꾸지 않음
- pending proposal은 추천 풀(find_similar)에 들어가지 않음
- rejected proposal은 추천 풀에 들어가지 않음
- approved/auto_passed 만 승격 후보가 됨
- page-level fallback은 indexable=false (callbacks.py 기존 정책 보존)
- 기존 legacy 추천 동작은 default mode 깨지지 않음
- manual=true cut 영역은 어떤 자동 path도 건드리지 않음 (재자르기 금지)

DB 무관 mock 기반 + 일부 모델 정의 검증. 다른 세션 미커밋 변경과 격리 실행.
"""
from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock

from apps.domains.matchup.models import (
    MatchupHitReportEntry,
    MatchupProblem,
    ProblemSegmentationProposal,
)
from apps.domains.matchup.services import eligible_for_recommendation_qs


class ProposalModelDefinitionTests(TestCase):
    """ProblemSegmentationProposal 모델 정의 자체가 운영 격리 원칙을 만족."""

    def test_status_choices_include_required_states(self):
        choices = dict(ProblemSegmentationProposal.STATUS_CHOICES)
        for required in ("pending", "needs_review", "rejected", "approved", "auto_passed"):
            self.assertIn(required, choices, f"missing status {required}")

    def test_engine_choices_cover_required_engines(self):
        choices = dict(ProblemSegmentationProposal.ENGINE_CHOICES)
        for required in ("yolo", "vlm", "ocr"):
            self.assertIn(required, choices, f"missing engine {required}")

    def test_default_status_is_pending(self):
        field = ProblemSegmentationProposal._meta.get_field("status")
        self.assertEqual(field.default, "pending")

    def test_promoted_problem_fk_set_null(self):
        """승인 후 MatchupProblem 삭제되어도 proposal 자체는 audit으로 보존."""
        from django.db.models.deletion import SET_NULL
        field = ProblemSegmentationProposal._meta.get_field("promoted_problem")
        self.assertEqual(field.remote_field.on_delete, SET_NULL)

    def test_validation_errors_default_is_list(self):
        """JSONField default=list — append('manual_overlap') 안전."""
        field = ProblemSegmentationProposal._meta.get_field("validation_errors")
        self.assertEqual(field.default, list)

    def test_proposal_no_selected_problem_ids_field(self):
        """proposal 모델 자체에 selected_problem_ids 필드 없음 — 운영 selection과 구조 분리."""
        field_names = {f.name for f in ProblemSegmentationProposal._meta.get_fields()}
        self.assertNotIn("selected_problem_ids", field_names)

    def test_hit_report_entry_unaffected_by_new_model(self):
        """ProblemSegmentationProposal 추가가 MatchupHitReportEntry 필드 변경하지 않음."""
        entry_fields = {f.name for f in MatchupHitReportEntry._meta.get_fields()}
        # Stage 2 / 기존 핵심 필드 그대로
        for required in (
            "id", "tenant", "report", "exam_problem",
            "selected_problem_ids", "comment", "order", "excluded",
            "selection_history", "last_modified_by",
        ):
            self.assertIn(required, entry_fields, f"missing field {required}")


class ProposalIsolationFromMatchupProblemTests(TestCase):
    """Proposal 인스턴스 자체가 MatchupProblem 추천 풀에 들어가지 않음."""

    def test_proposal_is_not_a_matchup_problem_subclass(self):
        """ProblemSegmentationProposal != MatchupProblem — find_similar 후보 X."""
        self.assertFalse(issubclass(ProblemSegmentationProposal, MatchupProblem))

    def test_proposal_has_separate_table(self):
        """proposal 모델은 별도 db_table — bulk SQL 격리."""
        proposal_table = ProblemSegmentationProposal._meta.db_table
        problem_table = MatchupProblem._meta.db_table
        self.assertNotEqual(proposal_table, problem_table)


class _ChainMock(MagicMock):
    """eligible_for_recommendation_qs() chain mock."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.exclude_calls: list[dict] = []
        self.filter_calls: list = []

    def exclude(self, **kw):
        self.exclude_calls.append(kw)
        return self

    def filter(self, *args, **kw):
        self.filter_calls.append({"args": args, "kw": kw})
        return self


class EligibilityStillExcludesProposalSignalsTests(TestCase):
    """Stage 0 blocklist는 여전히 작동 — proposal_status 마커 박힌 problem 풀 진입 X.

    proposal 모델 자체는 별도 테이블이라 자동 격리. 단 callback이
    MatchupProblem.meta에 proposal_status='pending' 등을 박는 future path도
    Stage 0 blocklist로 차단됨을 보장.
    """

    def test_pending_proposal_status_excluded(self):
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        self.assertIn(
            {"meta__contains": {"proposal_status": "pending"}},
            qs.exclude_calls,
        )

    def test_needs_review_excluded(self):
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        self.assertIn(
            {"meta__contains": {"proposal_status": "needs_review"}},
            qs.exclude_calls,
        )

    def test_rejected_excluded(self):
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        self.assertIn(
            {"meta__contains": {"proposal_status": "rejected"}},
            qs.exclude_calls,
        )

    def test_page_fallback_excluded(self):
        """page-level fallback은 indexable=false — 풀 진입 X (Phase 4 기존 정책)."""
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        self.assertIn(
            {"meta__contains": {"processing_quality": "page_fallback"}},
            qs.exclude_calls,
        )

    def test_legacy_null_metadata_passes_default_mode(self):
        """default mode에서 confirmation_status=NULL legacy problem 통과 (추천 0건 장애 방지)."""
        qs = _ChainMock()
        result = eligible_for_recommendation_qs(qs)
        # filter() 호출 0번 = strict allowlist 미적용 = legacy null 통과
        self.assertEqual(qs.filter_calls, [])
        self.assertIs(result, qs)


class ProposalManualOverlapPolicyTests(TestCase):
    """manual=true cut 영역과 겹치는 proposal 정책 — 모델 schema 가능 검증.

    실제 overlap 감지 로직은 callback path 변경 시 추가됨 (future Stage).
    이번 stage에서는 validation_errors JSONField가 manual_overlap 사유 기록할 수 있는
    schema만 보장.
    """

    def test_validation_errors_can_record_manual_overlap(self):
        """validation_errors는 list — 'manual_overlap' code 기록 가능."""
        proposal = ProblemSegmentationProposal(
            engine="yolo",
            validation_errors=[
                {"code": "manual_overlap", "detail": "bbox overlaps manual cut", "bbox_iou": 0.42}
            ],
        )
        self.assertEqual(len(proposal.validation_errors), 1)
        self.assertEqual(proposal.validation_errors[0]["code"], "manual_overlap")

    def test_status_rejected_with_manual_overlap_reason(self):
        """manual_overlap 발견 시 proposal status='rejected' + 사유 기록 가능 (모델 schema)."""
        proposal = ProblemSegmentationProposal(
            engine="vlm",
            status="rejected",
            validation_errors=[{"code": "manual_overlap", "bbox_iou": 0.55}],
        )
        self.assertEqual(proposal.status, "rejected")
        self.assertEqual(proposal.validation_errors[0]["code"], "manual_overlap")
