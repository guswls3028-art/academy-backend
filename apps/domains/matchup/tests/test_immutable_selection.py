"""Stage 2 (2026-05-06): MatchupHitReportEntry selected_problem_ids immutable guard.

학원장 어제 작성 보고서 selected_problem_ids 데이터 보호.

설계 검증 (mock 기반 unit test — 다른 세션 미커밋 schema 변경과 무관 실행):
- guard_selected_problem_ids signal handler 분기 검증
- append_selection_history 메서드 분기 검증

DB integration test 는 다른 세션 미커밋 변경 (core_tenant.video_max_sessions 등)
충돌로 SQLite/PG 모두 격리 실행 어려움 — 그 변경이 commit/migration 된 후 별도
HitReportEntriesUpsertView E2E 또는 integration test 추가 권장.
"""
from __future__ import annotations

from unittest import TestCase
from unittest.mock import MagicMock, patch

from apps.domains.matchup.models import MatchupHitReportEntry
from apps.domains.matchup.signals import (
    ALLOWED_SOURCES,
    FORBIDDEN_SOURCES,
    ImmutableSelectionError,
    guard_selected_problem_ids,
)


def _make_instance(pk: int | None, selected: list) -> MagicMock:
    """MatchupHitReportEntry 인스턴스 mock — pk/selected_problem_ids만 사용."""
    inst = MagicMock(spec=["pk", "selected_problem_ids", "_change_source"])
    inst.pk = pk
    inst.selected_problem_ids = selected
    # _change_source는 명시 안 박은 케이스 시뮬레이션을 위해 deleter
    if hasattr(inst, "_change_source"):
        del inst._change_source
    return inst


def _patch_prev(prev_selected: list):
    """sender.objects.only(...).get(pk=...) 가 prev_selected를 가진 인스턴스 반환하도록 patch."""
    prev = MagicMock()
    prev.selected_problem_ids = prev_selected
    qs = MagicMock()
    qs.get.return_value = prev
    objects = MagicMock()
    objects.only.return_value = qs
    return patch.object(MatchupHitReportEntry, "objects", objects)


class GuardSignalTests(TestCase):
    """pre_save signal handler 분기 검증."""

    def test_new_pk_none_passes(self):
        inst = _make_instance(pk=None, selected=[1, 2])
        # 신규 생성 — DB lookup 없이 통과
        guard_selected_problem_ids(sender=MatchupHitReportEntry, instance=inst)

    def test_noop_passes(self):
        inst = _make_instance(pk=1, selected=[1, 2])
        with _patch_prev([1, 2]):
            guard_selected_problem_ids(sender=MatchupHitReportEntry, instance=inst)

    def test_change_without_source_raises(self):
        inst = _make_instance(pk=1, selected=[1, 2, 3])
        with _patch_prev([1, 2]):
            with self.assertRaises(ImmutableSelectionError):
                guard_selected_problem_ids(sender=MatchupHitReportEntry, instance=inst)

    def test_forbidden_source_raises(self):
        for src in FORBIDDEN_SOURCES:
            inst = _make_instance(pk=1, selected=[5])
            inst._change_source = src
            with _patch_prev([1, 2]):
                with self.assertRaises(ImmutableSelectionError, msg=f"src={src}"):
                    guard_selected_problem_ids(sender=MatchupHitReportEntry, instance=inst)

    def test_allowed_source_passes(self):
        for src in ALLOWED_SOURCES:
            inst = _make_instance(pk=1, selected=[5])
            inst._change_source = src
            with _patch_prev([1, 2]):
                guard_selected_problem_ids(sender=MatchupHitReportEntry, instance=inst)

    def test_unknown_source_logged_not_raised(self):
        """allowlist 밖 source는 raise 하지 않고 logger.warning만."""
        inst = _make_instance(pk=1, selected=[5])
        inst._change_source = "experiment_x"
        with _patch_prev([1, 2]):
            with patch("apps.domains.matchup.signals.logger") as mock_log:
                guard_selected_problem_ids(sender=MatchupHitReportEntry, instance=inst)
                self.assertTrue(mock_log.warning.called)

    def test_strict_default_raises_missing_source(self):
        """guard mode default = strict, missing source raise."""
        import os
        prev_env = os.environ.pop("MATCHUP_SELECTION_GUARD_MODE", None)
        try:
            inst = _make_instance(pk=1, selected=[5])
            with _patch_prev([1, 2]):
                with self.assertRaises(ImmutableSelectionError):
                    guard_selected_problem_ids(sender=MatchupHitReportEntry, instance=inst)
        finally:
            if prev_env is not None:
                os.environ["MATCHUP_SELECTION_GUARD_MODE"] = prev_env

    def test_warn_mode_missing_source_passes(self):
        """ENV warn 모드는 missing source 통과 (logger.warning만)."""
        import os
        os.environ["MATCHUP_SELECTION_GUARD_MODE"] = "warn"
        try:
            inst = _make_instance(pk=1, selected=[5])
            with _patch_prev([1, 2]):
                with patch("apps.domains.matchup.signals.logger") as mock_log:
                    guard_selected_problem_ids(sender=MatchupHitReportEntry, instance=inst)
                    self.assertTrue(mock_log.warning.called)
        finally:
            os.environ.pop("MATCHUP_SELECTION_GUARD_MODE", None)

    def test_forbidden_source_raises_even_in_warn_mode(self):
        """warn 모드여도 forbidden source는 무조건 raise."""
        import os
        os.environ["MATCHUP_SELECTION_GUARD_MODE"] = "warn"
        try:
            inst = _make_instance(pk=1, selected=[5])
            inst._change_source = "ai_callback"
            with _patch_prev([1, 2]):
                with self.assertRaises(ImmutableSelectionError):
                    guard_selected_problem_ids(sender=MatchupHitReportEntry, instance=inst)
        finally:
            os.environ.pop("MATCHUP_SELECTION_GUARD_MODE", None)


class AppendSelectionHistoryTests(TestCase):
    """MatchupHitReportEntry.append_selection_history 분기 검증."""

    def test_noop_skip(self):
        """현재 값과 동일하면 history append 안 함."""
        e = MatchupHitReportEntry(selected_problem_ids=[1, 2], selection_history=[])
        e.append_selection_history(new_selected_ids=[1, 2], source="user_ui")
        self.assertEqual(e.selection_history, [])

    def test_history_append_on_change(self):
        e = MatchupHitReportEntry(selected_problem_ids=[1], selection_history=[])
        e.append_selection_history(
            new_selected_ids=[1, 2, 3],
            by_user_id=42,
            source="user_ui",
            reason="upsert",
        )
        self.assertEqual(len(e.selection_history), 1)
        h = e.selection_history[0]
        self.assertEqual(h["previous_selected_ids"], [1])
        self.assertEqual(h["new_selected_ids"], [1, 2, 3])
        self.assertEqual(h["changed_by_id"], 42)
        self.assertEqual(h["change_source"], "user_ui")
        self.assertEqual(h["reason"], "upsert")
        self.assertIn("timestamp", h)

    def test_does_not_modify_selected(self):
        """append_selection_history는 self.selected_problem_ids 자체는 미변경."""
        e = MatchupHitReportEntry(selected_problem_ids=[1], selection_history=[])
        e.append_selection_history(new_selected_ids=[2, 3], source="user_ui")
        self.assertEqual(e.selected_problem_ids, [1])

    def test_last_modified_by_set_when_user_id_present(self):
        e = MatchupHitReportEntry(selected_problem_ids=[1], selection_history=[])
        e.append_selection_history(new_selected_ids=[2], by_user_id=99, source="user_ui")
        self.assertEqual(e.last_modified_by_id, 99)

    def test_last_modified_by_unchanged_when_no_user_id(self):
        e = MatchupHitReportEntry(selected_problem_ids=[1], selection_history=[])
        e.last_modified_by_id = 7
        e.append_selection_history(new_selected_ids=[2], by_user_id=None, source="migration")
        self.assertEqual(e.last_modified_by_id, 7)

    def test_history_appends_cumulative(self):
        e = MatchupHitReportEntry(selected_problem_ids=[1], selection_history=[])
        e.append_selection_history(new_selected_ids=[2], source="user_ui")
        # 1차 변경 후 selected 갱신
        e.selected_problem_ids = [2]
        e.append_selection_history(new_selected_ids=[3, 4], source="user_ui")
        self.assertEqual(len(e.selection_history), 2)
        self.assertEqual(e.selection_history[0]["new_selected_ids"], [2])
        self.assertEqual(e.selection_history[1]["previous_selected_ids"], [2])
        self.assertEqual(e.selection_history[1]["new_selected_ids"], [3, 4])
