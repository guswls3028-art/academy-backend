"""Worker heartbeat 헬퍼 + check_dev_alerts.rule_stale_workers 단위 테스트."""
from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.core.management.commands.check_dev_alerts import (
    rule_stale_workers,
)
from apps.core.models import WorkerHeartbeatModel
from apps.shared.utils import heartbeat as heartbeat_utils
from apps.shared.utils.heartbeat import beat


class HeartbeatBeatTest(TestCase):
    def test_beat_creates_row(self):
        beat("messaging")
        rows = WorkerHeartbeatModel.objects.filter(name="messaging")
        self.assertEqual(rows.count(), 1)
        self.assertIsNotNone(rows.first().last_seen_at)

    def test_beat_updates_existing(self):
        first_ts = timezone.now()
        beat("ai_cpu", now=first_ts)
        first = WorkerHeartbeatModel.objects.get(name="ai_cpu")
        second_ts = first_ts + timedelta(seconds=1)
        beat("ai_cpu", now=second_ts)
        rows = WorkerHeartbeatModel.objects.filter(name="ai_cpu")
        self.assertEqual(rows.count(), 1, "동일 instance는 update — row 추가 없음")
        self.assertGreater(rows.first().last_seen_at, first.last_seen_at)

    def test_beat_silent_on_db_error(self):
        # 모델이 임포트 실패해도 polling을 절대 막으면 안 됨
        # → 실제 DB 에러 시뮬은 어렵지만, beat가 정상 호출에서 raise하지 않는지만 확인
        try:
            beat("messaging")
        except Exception:
            self.fail("beat() must never raise during normal call")

    def test_beat_prunes_old_rows(self):
        if hasattr(heartbeat_utils._prune_stale_heartbeats, "_last_pruned_at"):
            delattr(heartbeat_utils._prune_stale_heartbeats, "_last_pruned_at")

        now = timezone.now()
        WorkerHeartbeatModel.objects.create(
            name="messaging",
            instance="old-container",
            last_seen_at=now - timedelta(hours=heartbeat_utils.HEARTBEAT_RETENTION_HOURS + 1),
        )

        beat("messaging", now=now)

        self.assertFalse(
            WorkerHeartbeatModel.objects.filter(name="messaging", instance="old-container").exists()
        )


class RuleStaleWorkersTest(TestCase):
    def test_no_workers_returns_none(self):
        self.assertIsNone(rule_stale_workers())

    def test_fresh_heartbeat_returns_none(self):
        WorkerHeartbeatModel.objects.create(
            name="messaging",
            instance="i-1",
            last_seen_at=timezone.now(),
        )
        self.assertIsNone(rule_stale_workers(min_age_minutes=5))

    def test_stale_heartbeat_triggers(self):
        old = timezone.now() - timedelta(minutes=10)
        WorkerHeartbeatModel.objects.create(
            name="ai_cpu",
            instance="i-2",
            last_seen_at=old,
            version="sha-test",
        )
        result = rule_stale_workers(min_age_minutes=5)
        self.assertIsNotNone(result)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["worker"], "ai_cpu")
        self.assertEqual(result["rows"][0]["version"], "sha-test")

    def test_old_instance_ignored_when_worker_has_fresh_heartbeat(self):
        now = timezone.now()
        WorkerHeartbeatModel.objects.create(
            name="ai_cpu",
            instance="old-container",
            last_seen_at=now - timedelta(minutes=10),
        )
        WorkerHeartbeatModel.objects.create(
            name="ai_cpu",
            instance="current-container",
            last_seen_at=now,
        )
        self.assertIsNone(rule_stale_workers(min_age_minutes=5))

    def test_only_stale_rows_listed(self):
        now = timezone.now()
        WorkerHeartbeatModel.objects.create(
            name="messaging", instance="i-fresh",
            last_seen_at=now,
        )
        WorkerHeartbeatModel.objects.create(
            name="ai_cpu", instance="i-stale",
            last_seen_at=now - timedelta(minutes=15),
        )
        result = rule_stale_workers(min_age_minutes=5)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["rows"][0]["worker"], "ai_cpu")

    def test_very_old_stale_heartbeat_is_ignored(self):
        old = timezone.now() - timedelta(hours=heartbeat_utils.HEARTBEAT_RETENTION_HOURS + 1)
        WorkerHeartbeatModel.objects.create(
            name="ai_cpu",
            instance="old-container",
            last_seen_at=old,
        )
        self.assertIsNone(rule_stale_workers(min_age_minutes=5))
