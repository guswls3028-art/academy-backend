# PATH: apps/support/video/management/commands/validate_video_production_readiness.py
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

REGION = getattr(settings, "AWS_DEFAULT_REGION", None) or __import__("os").environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
QUEUE_NAME = getattr(settings, "VIDEO_BATCH_JOB_QUEUE", "academy-v1-video-batch-queue")
JOB_DEF_NAME = getattr(settings, "VIDEO_BATCH_JOB_DEFINITION", "academy-v1-video-batch-jobdef")
CE_NAME = getattr(settings, "VIDEO_BATCH_COMPUTE_ENV_NAME", "academy-v1-video-batch-ce")
RECONCILE_RULE = "academy-reconcile-video-jobs"
SCAN_STUCK_RULE = "academy-video-scan-stuck-rate"
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
        import boto3
        failures = []
        batch = None
        ce = []

        # Batch CE ACTIVE
        try:
            batch = boto3.client("batch", region_name=REGION)
            ce = batch.describe_compute_environments(computeEnvironments=[CE_NAME]).get("computeEnvironments") or []
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
        if batch:
            try:
                q = batch.describe_job_queues(jobQueues=[QUEUE_NAME]).get("jobQueues") or []
                if not q:
                    failures.append("Job queue not found")
                elif q[0].get("state") != "ENABLED":
                    failures.append(f"Job queue state={q[0].get('state')} (expected ENABLED)")
            except Exception as e:
                failures.append(f"Job queue check: {e}")

        # Job Definition ACTIVE
        if batch:
            try:
                jd = batch.describe_job_definitions(jobDefinitionName=JOB_DEF_NAME, status="ACTIVE").get("jobDefinitions") or []
                if not jd:
                    failures.append("Job definition not ACTIVE or not found")
            except Exception as e:
                failures.append(f"Job definition check: {e}")

        # Ops job definitions ACTIVE (reconcile, scanstuck)
        OPS_JOB_DEFS = ["academy-video-ops-reconcile", "academy-video-ops-scanstuck"]
        if batch:
            for od in OPS_JOB_DEFS:
                try:
                    jd = batch.describe_job_definitions(jobDefinitionName=od, status="ACTIVE").get("jobDefinitions") or []
                    if not jd:
                        failures.append(f"Ops job definition {od} not ACTIVE or not found")
                except Exception as e:
                    failures.append(f"Ops job definition {od}: {e}")

        # EventBridge reconcile + scan-stuck ENABLED
        try:
            events = boto3.client("events", region_name=REGION)
            for name in [RECONCILE_RULE, SCAN_STUCK_RULE]:
                r = events.describe_rule(Name=name)
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
                    boto3.client("iam").get_role(RoleName=service_role)
                if instance_role:
                    profile_name = instance_role.split("/")[-1]
                    boto3.client("iam").get_instance_profile(InstanceProfileName=profile_name)
            except Exception as e:
                failures.append(f"IAM roles check: {e}")

        # SSM parameter exists
        try:
            ssm = boto3.client("ssm", region_name=REGION)
            ssm.get_parameter(Name=SSM_PARAM)
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
                from libs.redis.client import get_redis_client
                r = get_redis_client()
                if r:
                    r.ping()
                else:
                    failures.append("Redis not configured (get_redis_client returned None)")
            except Exception as e:
                failures.append(f"Redis ping: {e}")

        # DB reachable
        try:
            connection.ensure_connection()
        except Exception as e:
            failures.append(f"DB: {e}")

        # CloudWatch alarms exist
        try:
            cw = boto3.client("cloudwatch", region_name=REGION)
            for alarm_name in ALARM_NAMES:
                alarms = cw.describe_alarms(AlarmNames=[alarm_name]).get("MetricAlarms") or []
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
