# PATH: apps/core/models/worker_heartbeat.py
"""Worker 프로세스 heartbeat — SQS 워커가 죽었는지 즉시 감지.

워커 polling 루프에서 cycle마다 1회 update.
check_dev_alerts cron이 stale row(N분+ 미갱신)을 감지해 Slack 알림.

설계 원칙:
- DB row 1개 / worker / instance. 동시 수십 인스턴스 운영해도 부담 없음.
- Redis 의존성 회피 (인프라 추가 X).
- 실패 시 silent skip — heartbeat 자체가 워커 polling을 막으면 안 됨.
"""
from django.db import models

from .base import TimestampModel


class WorkerHeartbeatModel(TimestampModel):
    """워커 프로세스 알람용 heartbeat.

    name: messaging / ai_cpu / ai_gpu / video — 워커 종류.
    instance: AWS instance-id 또는 hostname (다중 인스턴스 식별).
    last_seen_at: 마지막 polling cycle 시각. updated_at과 별개로 명시적 컬럼.
    version: 실행 중 image sha (sha-2a58b317 등). 배포 후 옛 인스턴스 검증용.
    """

    name = models.CharField(max_length=32, db_index=True)
    instance = models.CharField(max_length=64, default="")
    last_seen_at = models.DateTimeField(db_index=True)
    version = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "worker_heartbeat"
        constraints = [
            models.UniqueConstraint(
                fields=["name", "instance"],
                name="uniq_worker_heartbeat_name_instance",
            ),
        ]

    def __str__(self) -> str:
        return f"WorkerHeartbeat({self.name}@{self.instance}, {self.last_seen_at.isoformat()})"
