"""Stage 4 (2026-05-06): 추천 풀 자격 SSOT 단위 테스트.

eligible_for_recommendation_qs() 가 default/strict/manual_only mode 별로
올바른 queryset chain을 만드는지 mock 으로 검증.

DB 없이 실행. 다른 세션 미커밋 schema 변경(core_tenant.video_max_sessions 등)과
무관 격리 실행 가능.
"""
from __future__ import annotations

import os
from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.domains.matchup.services import eligible_for_recommendation_qs


class _ChainMock(MagicMock):
    """exclude/filter 등이 self를 반환하는 chainable mock."""

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


def _clear_env():
    for k in (
        "MATCHUP_RECOMMEND_STRICT_ALLOWLIST",
        "MATCHUP_RECOMMEND_MANUAL_ONLY",
    ):
        os.environ.pop(k, None)


class EligibilityDefaultModeTests(TestCase):
    """default mode = legacy null 통과, blocklist만 적용."""

    def setUp(self):
        _clear_env()

    def tearDown(self):
        _clear_env()

    def test_returns_chain_with_blocklist_excludes(self):
        qs = _ChainMock()
        result = eligible_for_recommendation_qs(qs)
        self.assertIs(result, qs)
        kw_list = qs.exclude_calls
        self.assertEqual(len(kw_list), 9, f"expected 9 excludes, got {len(kw_list)}: {kw_list}")

    def test_low_quality_excluded(self):
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        self.assertIn({"meta__contains": {"low_quality": True}}, qs.exclude_calls)

    def test_indexable_false_excluded(self):
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        self.assertIn({"document__meta__contains": {"indexable": False}}, qs.exclude_calls)

    def test_proposal_pending_excluded(self):
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        self.assertIn({"meta__contains": {"proposal_status": "pending"}}, qs.exclude_calls)

    def test_proposal_needs_review_excluded(self):
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        self.assertIn({"meta__contains": {"proposal_status": "needs_review"}}, qs.exclude_calls)

    def test_proposal_rejected_excluded(self):
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        self.assertIn({"meta__contains": {"proposal_status": "rejected"}}, qs.exclude_calls)

    def test_processing_quality_failed_excluded(self):
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        self.assertIn(
            {"meta__contains": {"processing_quality": "failed"}}, qs.exclude_calls,
        )

    def test_processing_quality_page_fallback_excluded(self):
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        self.assertIn(
            {"meta__contains": {"processing_quality": "page_fallback"}},
            qs.exclude_calls,
        )

    def test_default_does_not_apply_strict_filter(self):
        """default mode에서는 confirmation_status filter 추가 X (legacy null 통과)."""
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        self.assertEqual(qs.filter_calls, [])


class EligibilityStrictModeTests(TestCase):
    """strict allowlist mode = confirmation_status='confirmed' OR manual=True."""

    def setUp(self):
        _clear_env()
        os.environ["MATCHUP_RECOMMEND_STRICT_ALLOWLIST"] = "1"

    def tearDown(self):
        _clear_env()

    def test_strict_mode_adds_filter(self):
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        self.assertEqual(len(qs.filter_calls), 1)

    def test_strict_filter_includes_confirmed_or_manual(self):
        from django.db.models import Q
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        call = qs.filter_calls[0]
        self.assertEqual(len(call["args"]), 1)
        q_arg = call["args"][0]
        self.assertIsInstance(q_arg, Q)
        # Q(confirmed) | Q(manual) — children에 두 항목 + connector OR
        self.assertEqual(q_arg.connector, "OR")
        children_str = str(q_arg.children)
        self.assertIn("confirmation_status", children_str)
        self.assertIn("confirmed", children_str)
        self.assertIn("manual", children_str)


class EligibilityManualOnlyModeTests(TestCase):
    """manual_only mode = manual=True 만."""

    def setUp(self):
        _clear_env()
        os.environ["MATCHUP_RECOMMEND_MANUAL_ONLY"] = "1"

    def tearDown(self):
        _clear_env()

    def test_manual_only_adds_filter(self):
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        self.assertEqual(len(qs.filter_calls), 1)
        call = qs.filter_calls[0]
        self.assertEqual(call["kw"], {"meta__contains": {"manual": True}})


class EligibilityCombinedModeTests(TestCase):
    """strict + manual_only 동시 ON — manual_only 가 strict 부분집합."""

    def setUp(self):
        _clear_env()
        os.environ["MATCHUP_RECOMMEND_STRICT_ALLOWLIST"] = "1"
        os.environ["MATCHUP_RECOMMEND_MANUAL_ONLY"] = "1"

    def tearDown(self):
        _clear_env()

    def test_both_filters_applied(self):
        qs = _ChainMock()
        eligible_for_recommendation_qs(qs)
        # strict 1번 + manual_only 1번 = 2번
        self.assertEqual(len(qs.filter_calls), 2)
