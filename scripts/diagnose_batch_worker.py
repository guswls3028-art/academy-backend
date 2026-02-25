# scripts/diagnose_batch_worker.py
"""
One-take diagnostic for "worker not starting" (RUNNABLE stuck).
Checks: RUNNABLE jobs + statusReason, CE state/scaling, JobDef image vs ECR,
ECR pull/arch mismatch, IAM (Batch/ECR/logs on compute).
Outputs: ROOT CAUSE, FIX PLAN, COMMANDS TO APPLY.
Run from repo root; uses boto3. Region from AWS_REGION/AWS_DEFAULT_REGION or ap-northeast-2.
"""
from __future__ import annotations

import os
import sys

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-northeast-2"
QUEUE_NAME = "academy-video-batch-queue"
JOB_DEF_NAME = "academy-video-batch-jobdef"
CE_NAME_PREFIX = "academy-video-batch-ce"
LOG_GROUP = "/aws/batch/academy-video-worker"


def main() -> None:
    out: list[str] = []
    issues: list[str] = []
    fix_plan: list[str] = []
    commands: list[str] = []
    runnable: list = []
    jd = None
    ce_name = None

    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        print("ROOT CAUSE: boto3 not installed (pip install boto3)")
        print("FIX PLAN:")
        print("  - pip install boto3")
        print("COMMANDS TO APPLY:")
        print("  pip install boto3")
        sys.exit(1)

    batch = boto3.client("batch", region_name=REGION)
    ecr = boto3.client("ecr", region_name=REGION)
    logs = boto3.client("logs", region_name=REGION)

    try:
        qs = batch.describe_job_queues(jobQueues=[QUEUE_NAME]).get("jobQueues") or []
        queue = qs[0] if qs else None
    except Exception as e:
        queue = None
        issues.append(f"Queue describe failed: {e}")

    if queue:
        order = queue.get("computeEnvironmentOrder") or []
        if order:
            arn = order[0].get("computeEnvironment", "")
            ce_name = arn.split("/")[-1] if "/" in arn else arn.split(":")[-1]
        out.append(f"Queue: {queue.get('jobQueueName')} state={queue.get('state')} status={queue.get('status')}")
    else:
        issues.append("Queue not found or not ENABLED")
        out.append("Queue: NOT FOUND or error")

    out.append("")
    out.append("--- RUNNABLE jobs ---")
    try:
        list_r = batch.list_jobs(jobQueue=QUEUE_NAME, jobStatus="RUNNABLE", maxResults=10)
        runnable = list_r.get("jobSummaryList") or []
        if not runnable:
            out.append("No RUNNABLE jobs.")
        else:
            for s in runnable:
                jid = s.get("jobId")
                desc = batch.describe_jobs(jobs=[jid]).get("jobs") or []
                if desc:
                    j = desc[0]
                    out.append(f"  jobId={jid} status={j.get('status')} statusReason={j.get('statusReason', '')}")
                    out.append(f"    createdAt={j.get('createdAt')} jobDefinition={j.get('jobDefinition')}")
                    container = j.get("container", {}) or {}
                    out.append(f"    image={container.get('image')}")
                else:
                    out.append(f"  jobId={jid} (describe failed)")
            issues.append(f"{len(runnable)} job(s) stuck RUNNABLE; check statusReason and CE capacity")
    except Exception as e:
        out.append(f"  list_jobs RUNNABLE failed: {e}")
        issues.append(str(e))

    out.append("")
    out.append("--- Compute environment ---")
    if not ce_name:
        try:
            ces = batch.describe_compute_environments().get("computeEnvironments") or []
            for c in ces:
                if "academy-video" in (c.get("computeEnvironmentName") or ""):
                    ce_name = c["computeEnvironmentName"]
                    break
        except Exception as e:
            out.append(f"  describe_compute_environments failed: {e}")
    if ce_name:
        try:
            ces = batch.describe_compute_environments(computeEnvironments=[ce_name]).get("computeEnvironments") or []
            ce = ces[0] if ces else None
            if ce:
                cr = ce.get("computeResources") or {}
                out.append(f"  CE: {ce.get('computeEnvironmentName')} state={ce.get('state')} status={ce.get('status')}")
                out.append(f"  desiredvCpus={cr.get('desiredvCpus')} min={cr.get('minvCpus')} max={cr.get('maxvCpus')}")
                out.append(f"  instanceTypes={cr.get('instanceTypes')}")
                if ce.get("state") != "ENABLED" or ce.get("status") != "VALID":
                    issues.append("CE not ENABLED/VALID")
                if (cr.get("desiredvCpus") or 0) == 0 and runnable:
                    issues.append("CE desiredvCpus=0 while jobs RUNNABLE (scaling not launching)")
            else:
                out.append(f"  CE {ce_name} not found")
        except Exception as e:
            out.append(f"  describe CE failed: {e}")
            issues.append(str(e))
    else:
        out.append("  CE name could not be resolved from queue")
        issues.append("CE name unknown")

    out.append("")
    out.append("--- Job definition vs ECR ---")
    try:
        jds = batch.describe_job_definitions(jobDefinitionName=JOB_DEF_NAME, status="ACTIVE").get("jobDefinitions") or []
        jd = max(jds, key=lambda x: x.get("revision", 0)) if jds else None
        if jd:
            img = (jd.get("containerProperties") or {}).get("image", "")
            out.append(f"  JobDef: {jd.get('jobDefinitionName')}:{jd.get('revision')} image={img}")
            if img and "amazonaws.com" in img:
                repo_tag = img.split("/")[-1]
                if ":" in repo_tag:
                    repo, tag = repo_tag.split(":", 1)
                    try:
                        di = ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": tag}]).get("imageDetails") or []
                        if di:
                            digest = di[0].get("imageDigest", "")
                            out.append(f"  ECR: repo={repo} tag={tag} digest={digest[:20]}...")
                        else:
                            issues.append(f"ECR image not found: {repo}:{tag}")
                    except ClientError as e:
                        err = e.response.get("Error", {}).get("Code", "")
                        if err == "ImageNotFoundException":
                            issues.append(f"ECR ImageNotFoundException: {repo}:{tag}")
                        elif "AccessDenied" in str(e):
                            issues.append("ECR AccessDenied (diagnostic script role)")
                        else:
                            issues.append(f"ECR error: {e}")
        else:
            out.append("  No ACTIVE job definition found")
            issues.append("No ACTIVE job definition")
    except Exception as e:
        out.append(f"  JobDef/ECR check failed: {e}")
        issues.append(str(e))

    out.append("")
    out.append("--- Architecture ---")
    if ce_name and jd:
        try:
            ces = batch.describe_compute_environments(computeEnvironments=[ce_name]).get("computeEnvironments") or []
            cr = (ces[0].get("computeResources") or {}) if ces else {}
            types = cr.get("instanceTypes") or []
            arm = any("g" in (t or "") for t in types)
            out.append(f"  CE instanceTypes: {types} -> ARM64={arm}")
            if not arm:
                out.append("  WARN: CE is x86; image from academy-build-arm64 is ARM64 -> mismatch")
                issues.append("Architecture mismatch: CE x86 vs image ARM64")
        except Exception as e:
            out.append(f"  Arch check: {e}")
    else:
        out.append("  Skipped (no CE or JobDef)")

    out.append("")
    out.append("--- IAM (compute node) ---")
    out.append("  Batch compute: ECS instance role + execution role need ecr:GetAuthorizationToken, ecr:BatchGetImage, ecr:GetDownloadUrlForLayer, logs:CreateLogStream, logs:PutLogEvents.")

    out.append("")
    out.append("--- CloudWatch (recent errors) ---")
    try:
        streams = logs.describe_log_streams(
            logGroupName=LOG_GROUP,
            orderBy="LastEventTime",
            descending=True,
            limit=5,
        ).get("logStreams") or []
        for s in streams[:3]:
            name = s.get("logStreamName", "")
            events = logs.get_log_events(logGroupName=LOG_GROUP, logStreamName=name, limit=50).get("events") or []
            for ev in events:
                msg = (ev.get("message") or "").lower()
                if "cannotpullcontainererror" in msg or "manifest unknown" in msg or "no basic auth" in msg or "access denied" in msg or "exec format" in msg:
                    out.append(f"  [{name}] {ev.get('message', '')[:120]}")
    except Exception as e:
        out.append(f"  Logs: {e}")

    for line in out:
        print(line)

    root = issues[0] if len(issues) == 1 else "; ".join(issues[:3]) if issues else "No obvious issue; check RUNNABLE statusReason and CE scaling in console."

    print("")
    print("========== ROOT CAUSE ==========")
    print(root)
    print("")
    print("========== FIX PLAN ==========")
    if "RUNNABLE" in root or "desiredvCpus" in root:
        fix_plan.append("Ensure CE is ENABLED and maxvCpus >= 1; check scaling events.")
    if "ECR" in root or "image" in root.lower():
        fix_plan.append("Push image: .\\scripts\\build_and_push_ecr_remote.ps1 -VideoWorkerOnly")
        fix_plan.append("Register JobDef: run batch_video_setup.ps1 with same ECR URI.")
    if "Architecture" in root or "mismatch" in root:
        fix_plan.append("Use CE instanceTypes matching image: c6g/m6g for ARM64.")
    if "IAM" in root or "AccessDenied" in root:
        fix_plan.append("Attach ECR + logs to academy-batch-ecs-task-execution-role and instance role.")
    if not fix_plan:
        fix_plan.append("Run scripts\\fix_and_redeploy_video_worker.ps1 for full apply.")
    for b in fix_plan:
        print(f"  - {b}")
    print("")
    print("========== COMMANDS TO APPLY ==========")
    commands.append("aws batch describe-compute-environments --compute-environments " + (ce_name or "academy-video-batch-ce-v2") + " --region " + REGION)
    commands.append("aws batch list-jobs --job-queue " + QUEUE_NAME + " --job-status RUNNABLE --region " + REGION)
    commands.append(".\\scripts\\fix_and_redeploy_video_worker.ps1")
    for c in commands:
        print(f"  {c}")


if __name__ == "__main__":
    main()
