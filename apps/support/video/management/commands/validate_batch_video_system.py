# PATH: apps/support/video/management/commands/validate_batch_video_system.py
"""
AWS Batch video transcoding system health check.
Run: python manage.py validate_batch_video_system
Requires: DB, AWS CLI + credentials for Steps 2,3,5,6.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone, timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand

REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-northeast-2")
LOG_GROUP = "/aws/batch/academy-video-worker"
LAMBDA_QUEUE_DEPTH = "academy-worker-queue-depth-metric"
LAMBDA_AUTOSCALE = "academy-worker-autoscale"
VALID_STATES = {"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "RETRY_WAIT", "DEAD", "CANCELLED"}
SUCCESS_STATE = "SUCCEEDED"
HEARTBEAT_MAX_MINUTES = 15


def run_aws_batch_describe(job_id: str):
    r = subprocess.run(
        [
            "aws", "batch", "describe-jobs", "--jobs", job_id,
            "--region", REGION,
            "--query", "jobs[0].{status:status, exitCode:container.exitCode, reason:statusReason}",
            "--output", "json",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if r.returncode != 0:
        return None, r.stderr
    try:
        return json.loads(r.stdout), None
    except Exception as e:
        return None, str(e)


def run_cloudwatch_logs():
    r = subprocess.run(
        [
            "aws", "logs", "describe-log-streams",
            "--log-group-name", LOG_GROUP,
            "--order-by", "LastEventTime", "--descending", "--limit", "10",
            "--region", REGION,
            "--query", "logStreams[*].logStreamName", "--output", "json",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if r.returncode != 0:
        return False, False, r.stderr
    try:
        streams = json.loads(r.stdout)
    except Exception:
        return False, False, "parse streams failed"
    if not streams:
        return False, False, "no log streams"
    found_start = found_completed = False
    for stream in streams[:5]:
        r2 = subprocess.run(
            [
                "aws", "logs", "get-log-events",
                "--log-group-name", LOG_GROUP, "--log-stream-name", stream,
                "--region", REGION, "--limit", "500", "--output", "json",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r2.returncode != 0:
            continue
        try:
            for ev in json.loads(r2.stdout).get("events", []):
                msg = ev.get("message", "")
                if "BATCH_PROCESS_START" in msg:
                    found_start = True
                if "BATCH_JOB_COMPLETED" in msg:
                    found_completed = True
                if found_start and found_completed:
                    return True, True, None
        except Exception:
            continue
    return found_start, found_completed, None


def run_lambda_config(name: str):
    r = subprocess.run(
        [
            "aws", "lambda", "get-function-configuration",
            "--function-name", name, "--region", REGION,
            "--query", "Environment.Variables", "--output", "json",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if r.returncode != 0:
        return None, r.stderr
    try:
        return (json.loads(r.stdout) if r.stdout.strip() else {}), None
    except Exception as e:
        return None, str(e)


def run_iam_role(role_name: str):
    r = subprocess.run(
        ["aws", "iam", "get-role", "--role-name", role_name, "--output", "json"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if r.returncode != 0:
        return False, r.stderr
    r2 = subprocess.run(
        ["aws", "iam", "list-attached-role-policies", "--role-name", role_name, "--output", "json"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if r2.returncode != 0:
        return False, r2.stderr
    try:
        return len(json.loads(r2.stdout).get("AttachedPolicies", [])) >= 1, None
    except Exception as e:
        return False, str(e)


class Command(BaseCommand):
    help = "Validate AWS Batch video transcoding system (DB, Batch, Logs, Stuck scan, Lambda, IAM)."

    def handle(self, *args, **options):
        from apps.support.video.models import VideoTranscodeJob

        db_ok = batch_ok = log_ok = stuck_ok = lambda_ok = iam_ok = False

        # --- STEP 1 ---
        qs = VideoTranscodeJob.objects.order_by("-created_at")[:3]
        jobs = []
        for j in qs:
            jobs.append({
                "id": str(j.id),
                "state": j.state,
                "aws_batch_job_id": j.aws_batch_job_id or "",
                "attempt_count": j.attempt_count,
                "error_code": j.error_code or "",
                "error_message": (j.error_message or "")[:200],
                "heartbeat": j.last_heartbeat_at.isoformat() if j.last_heartbeat_at else None,
                "updated_at": j.updated_at.isoformat(),
            })
        self.stdout.write("STEP 1 Latest jobs: " + json.dumps(jobs, indent=2, default=str))

        if not jobs:
            db_ok = True
        else:
            db_ok = True
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=HEARTBEAT_MAX_MINUTES)
            for j in jobs:
                if not (j.get("aws_batch_job_id") or "").strip():
                    db_ok = False
                    break
                if j.get("state", "") not in VALID_STATES:
                    db_ok = False
                    break
                if j.get("state") == SUCCESS_STATE and (j.get("error_code") or "").strip():
                    db_ok = False
                    break
                if j.get("state") == "RUNNING" and j.get("heartbeat"):
                    try:
                        hb = datetime.fromisoformat(j["heartbeat"].replace("Z", "+00:00"))
                        if hb < cutoff:
                            db_ok = False
                            break
                    except Exception:
                        db_ok = False
                        break
        if not db_ok:
            self.stdout.write(self.style.WARNING("STEP 1 Validation rules failed for above jobs."))

        # --- STEP 2 ---
        batch_ok = True
        for j in jobs:
            aws_id = (j.get("aws_batch_job_id") or "").strip()
            if not aws_id:
                continue
            info, err2 = run_aws_batch_describe(aws_id)
            self.stdout.write("STEP 2 Job " + aws_id + " -> " + (json.dumps(info) if info else str(err2)))
            if err2:
                batch_ok = False
            elif info:
                if info.get("status") == "FAILED":
                    batch_ok = False
                elif info.get("status") == "SUCCEEDED" and info.get("exitCode") != 0:
                    batch_ok = False

        # --- STEP 3 ---
        found_start, found_completed, log_err = run_cloudwatch_logs()
        if log_err:
            self.stdout.write("STEP 3 CloudWatch error: " + str(log_err))
        log_ok = found_start and found_completed
        self.stdout.write("STEP 3 Logs: BATCH_PROCESS_START=%s BATCH_JOB_COMPLETED=%s" % (found_start, found_completed))

        # --- STEP 4 ---
        out = StringIO()
        try:
            call_command("scan_stuck_video_jobs", "--dry-run", stdout=out)
            stuck_ok = True
        except Exception as e:
            self.stdout.write("STEP 4 scan_stuck_video_jobs FAIL: " + str(e))
        else:
            self.stdout.write("STEP 4 scan_stuck_video_jobs (dry-run): OK " + out.getvalue()[:300])

        # --- STEP 5 ---
        vars1, e1 = run_lambda_config(LAMBDA_QUEUE_DEPTH)
        vars2, e2 = run_lambda_config(LAMBDA_AUTOSCALE)
        if e1 or e2:
            self.stdout.write("STEP 5 Lambda config error: " + (e1 or e2 or ""))
        else:
            v1, v2 = vars1 or {}, vars2 or {}
            enable_video_metrics = v1.get("ENABLE_VIDEO_METRICS", "false").lower() == "true"
            enable_video_wake = v2.get("ENABLE_VIDEO_WAKE", "false").lower() == "true"
            lambda_ok = not enable_video_metrics and not enable_video_wake
            self.stdout.write("STEP 5 queue_depth ENABLE_VIDEO_METRICS: %s" % v1.get("ENABLE_VIDEO_METRICS", "(missing=ok)"))
            self.stdout.write("STEP 5 autoscale ENABLE_VIDEO_WAKE: %s" % v2.get("ENABLE_VIDEO_WAKE", "(missing=ok)"))

        # --- STEP 6 ---
        r1, e1 = run_iam_role("academy-video-batch-job-role")
        r2, e2 = run_iam_role("academy-batch-ecs-task-execution-role")
        iam_ok = r1 and r2
        self.stdout.write("STEP 6 academy-video-batch-job-role: %s academy-batch-ecs-task-execution-role: %s" % (r1, r2))
        if not iam_ok:
            self.stdout.write("STEP 6 IAM error: " + (e1 or e2 or ""))

        # --- OUTPUT ---
        self.stdout.write("")
        self.stdout.write("=" * 60)
        self.stdout.write("DB_CHECK: " + ("OK" if db_ok else "FAIL"))
        self.stdout.write("BATCH_CHECK: " + ("OK" if batch_ok else "FAIL"))
        self.stdout.write("LOG_CHECK: " + ("OK" if log_ok else "FAIL"))
        self.stdout.write("STUCK_CHECK: " + ("OK" if stuck_ok else "FAIL"))
        self.stdout.write("LAMBDA_CHECK: " + ("OK" if lambda_ok else "FAIL"))
        self.stdout.write("IAM_CHECK: " + ("OK" if iam_ok else "FAIL"))
        all_ok = db_ok and batch_ok and log_ok and stuck_ok and lambda_ok and iam_ok
        self.stdout.write("SYSTEM_STATUS: " + ("FULLY_STABLE" if all_ok else "NEEDS_ATTENTION"))
        self.stdout.write("=" * 60)
