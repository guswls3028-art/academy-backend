"""
Production completeness check: Batch CE ACTIVE, Job Queue ENABLED, Job Definition ACTIVE,
EventBridge reconcile + scan-stuck ENABLED, IAM roles attached, SSM parameter exists,
Redis reachable (if configured), DB reachable, CloudWatch alarms exist.
Print PRODUCTION READY: YES / NO. Exit non-zero if any missing.
"""

from __future__ import annotations

import sys
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import connection

from academy.adapters.cache.redis_video_status_cache import redis_ping
from academy.adapters.compute.aws_video_ops import (
    describe_batch_compute_environments,
    describe_batch_job_definitions,
    describe_batch_job_queues,
    describe_cloudwatch_alarms,
    describe_event_rule,
    get_ssm_parameter_value,
    iam_instance_profile_exists,
    iam_role_exists,
)

REGION = getattr(settings, "AWS_DEFAULT_REGION", None) or __import__("os").environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
QUEUE_NAME = getattr(settings, "VIDEO_BATCH_JOB_QUEUE", "academy-v1-video-batch-queue")
JOB_DEF_NAME = getattr(settings, "VIDEO_BATCH_JOB_DEFINITION", "academy-v1-video-batch-jobdef")
CE_NAME = getattr(settings, "VIDEO_BATCH_COMPUTE_ENV_NAME", "academy-v1-video-batch-ce-200gb")
RECONCILE_RULE = getattr(settings, "VIDEO_RECONCILE_RULE_NAME", "academy-v1-reconcile-video-jobs")
SCAN_STUCK_RULE = getattr(settings, "VIDEO_SCAN_STUCK_RULE_NAME", "academy-v1-video-scan-stuck-rate")
OPS_JOB_DEFS = list(getattr(settings, "VIDEO_OPS_JOB_DEFS", ("academy-v1-video-ops-reconcile", "academy-v1-video-ops-scanstuck")))
SSM_PARAM = "/academy/workers/env"
ALARM_NAMES = [
    "academy-video-DeadJobs",
    "academy-video-UploadFailures",
    "academy-video-FailedJobs",
    "academy-video-BatchJobFailures",
    "academy-video-QueueRunnable",
]


class Command(BaseCommand):
    help = "Verify all video production dependencies; print PRODUCTION READY: YES/NO"

    def handle(self, *args, **options):
        failures = []
        ce = []

        # Batch CE ACTIVE
        try:
            ce = describe_batch_compute_environments(names=[CE_NAME], region=REGION)
            if not ce:
                failures.append("Batch compute environment not found")
            else:
                c = ce[0]
                if c.get("status") != "VALID":
                    failures.append(f"Batch CE status={c.get('status')} (expected VALID)")
                if c.get("state") != "ENABLED":
                    failures.append(f"Batch CE state={c.get('state')} (expected ENABLED)")
        except Exception as e:
            failures.append(f"Batch CE check: {e}")

        # Job Queue ENABLED
        try:
            q = describe_batch_job_queues(names=[QUEUE_NAME], region=REGION)
            if not q:
                failures.append("Job queue not found")
            elif q[0].get("state") != "ENABLED":
                failures.append(f"Job queue state={q[0].get('state')} (expected ENABLED)")
        except Exception as e:
            failures.append(f"Job queue check: {e}")

        # Job Definition ACTIVE
        try:
            jd = describe_batch_job_definitions(name=JOB_DEF_NAME, status="ACTIVE", region=REGION)
            if not jd:
                failures.append("Job definition not ACTIVE or not found")
        except Exception as e:
            failures.append(f"Job definition check: {e}")

        # Ops job definitions ACTIVE (reconcile, scanstuck)
        for od in OPS_JOB_DEFS:
            try:
                jd = describe_batch_job_definitions(name=od, status="ACTIVE", region=REGION)
                if not jd:
                    failures.append(f"Ops job definition {od} not ACTIVE or not found")
            except Exception as e:
                failures.append(f"Ops job definition {od}: {e}")

        # EventBridge reconcile + scan-stuck ENABLED
        try:
            for name in [RECONCILE_RULE, SCAN_STUCK_RULE]:
                r = describe_event_rule(name=name, region=REGION)
                if r.get("State") != "ENABLED":
                    failures.append(f"EventBridge rule {name} not ENABLED")
        except Exception as e:
            failures.append(f"EventBridge check: {e}")

        # IAM roles attached (CE uses service role and instance role)
        if ce:
            try:
                c = ce[0]
                service_role = (c.get("serviceRole") or "").split("/")[-1]
                instance_role = (c.get("computeResources") or {}).get("instanceRole") or ""
                if service_role:
                    iam_role_exists(role_name=service_role)
                if instance_role:
                    profile_name = instance_role.split("/")[-1]
                    iam_instance_profile_exists(profile_name=profile_name)
            except Exception as e:
                failures.append(f"IAM roles check: {e}")

        # SSM parameter exists
        try:
            get_ssm_parameter_value(name=SSM_PARAM, region=REGION)
        except Exception as e:
            err = getattr(e, "response", {}).get("Error", {}).get("Code", "")
            if err in ("ParameterNotFound", "InvalidParameter"):
                failures.append("SSM parameter /academy/workers/env not found")
            else:
                failures.append(f"SSM check: {e}")

        # Redis reachable (if configured)
        redis_host = getattr(settings, "REDIS_HOST", None) or __import__("os").environ.get("REDIS_HOST")
        if redis_host:
            try:
                ok = redis_ping()
                if ok is None:
                    failures.append("Redis not configured (get_redis_client returned None)")
                elif not ok:
                    failures.append("Redis ping returned false")
            except Exception as e:
                failures.append(f"Redis ping: {e}")

        # DB reachable
        try:
            connection.ensure_connection()
        except Exception as e:
            failures.append(f"DB: {e}")

        # CloudWatch alarms exist
        try:
            for alarm_name in ALARM_NAMES:
                alarms = describe_cloudwatch_alarms(alarm_names=[alarm_name], region=REGION)
                if not alarms:
                    failures.append(f"CloudWatch alarm {alarm_name} not found")
        except Exception as e:
            failures.append(f"CloudWatch alarms: {e}")

        if failures:
            self.stdout.write(self.style.ERROR("PRODUCTION READY: NO"))
            for f in failures:
                self.stdout.write(self.style.ERROR(f"  - {f}"))
            sys.exit(1)
        self.stdout.write(self.style.SUCCESS("PRODUCTION READY: YES"))
