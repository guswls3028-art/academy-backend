# scripts/diagnose_batch_worker.py
"""
One-take diagnostic for "worker not starting" (RUNNABLE stuck).
Checks: RUNNABLE jobs + statusReason, CE state/scaling, JobDef image vs ECR,
ECR pull/arch mismatch, IAM (Batch/ECR/logs on compute).
Outputs: ROOT CAUSE (one line), FIX PLAN (bullets), COMMANDS TO APPLY (exact).
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


def _check_root_credentials() -> None:
    """Exit with code 3 if caller identity ARN contains ':root'."""
    try:
        import boto3
        sts = boto3.client("sts", region_name=REGION)
        ident = sts.get_caller_identity()
        arn = (ident.get("Arn") or "").strip()
        if ":root" in arn:
            print("ROOT CAUSE: Running with root credentials (unsafe, not representative of production roles)")
            sys.exit(3)
    except Exception:
        pass


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
    iam = boto3.client("iam")

    _check_root_credentials()

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
            runnable_issue_added = False
            for s in runnable:
                jid = s.get("jobId")
                desc = batch.describe_jobs(jobs=[jid]).get("jobs") or []
                if desc:
                    j = desc[0]
                    status_reason = j.get("statusReason") or ""
                    out.append(f"  jobId={jid} status={j.get('status')} statusReason={status_reason}")
                    out.append(f"    createdAt={j.get('createdAt')} jobDefinition={j.get('jobDefinition')}")
                    container = j.get("container", {}) or {}
                    out.append(f"    image={container.get('image')}")
                    if not runnable_issue_added:
                        if not status_reason:
                            issues.append("RUNNABLE with empty statusReason; CE may not be scaling or image/arch issue")
                        elif "MISCONFIGURATION" in status_reason:
                            issues.append(f"RUNNABLE MISCONFIGURATION: {status_reason[:200]}")
                        else:
                            issues.append(f"{len(runnable)} job(s) stuck RUNNABLE; check CE capacity and image/arch")
                        runnable_issue_added = True
                else:
                    out.append(f"  jobId={jid} (describe failed)")
    except Exception as e:
        out.append(f"  list_jobs RUNNABLE failed: {e}")
        issues.append(str(e))

    out.append("")
    out.append("--- Compute environment ---")
    if not ce_name:
        try:
            ces = batch.describe_compute_environments().get("computeEnvironments") or []
            for c in ces:
                if "academy-video" in (c.get("computeEnvironmentName") or "") and "ops" not in (c.get("computeEnvironmentName") or "").lower():
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
                desired = cr.get("desiredvCpus") or 0
                minv = cr.get("minvCpus") or 0
                maxv = cr.get("maxvCpus") or 0
                out.append(f"  CE: {ce.get('computeEnvironmentName')} state={ce.get('state')} status={ce.get('status')}")
                out.append(f"  desiredvCpus={desired} minvCpus={minv} maxvCpus={maxv}")
                out.append(f"  instanceTypes={cr.get('instanceTypes')}")
                if ce.get("state") != "ENABLED" or ce.get("status") != "VALID":
                    issues.append("CE not ENABLED/VALID")
                if desired == 0 and runnable:
                    issues.append("CE desiredvCpus=0 while jobs RUNNABLE (scaling not launching or maxvCpus=0)")
                if maxv < 1:
                    issues.append("CE maxvCpus < 1; cannot run any job")
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
                            out.append(f"  ECR (JobDef tag): repo={repo} tag={tag} digest={digest[:24]}...")
                        else:
                            issues.append(f"ECR image not found for JobDef tag: {repo}:{tag}")
                    except ClientError as e:
                        err = e.response.get("Error", {}).get("Code", "")
                        if err == "ImageNotFoundException":
                            issues.append(f"ECR ImageNotFoundException: {repo}:{tag}")
                        elif "AccessDenied" in str(e):
                            issues.append("ECR AccessDenied (diagnostic script role)")
                        else:
                            issues.append(f"ECR error: {e}")
                    try:
                        latest = ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": "latest"}]).get("imageDetails") or []
                        if latest:
                            ecr_latest_digest = latest[0].get("imageDigest", "")
                            jd_di = ecr.describe_images(repositoryName=repo, imageIds=[{"imageTag": tag}]).get("imageDetails") or []
                            if jd_di and jd_di[0].get("imageDigest") == ecr_latest_digest:
                                out.append(f"  ECR :latest digest matches JobDef image (up to date)")
                            else:
                                out.append(f"  ECR :latest digest differs from JobDef tag; consider registering new revision with :latest")
                                issues.append("JobDef image tag may not match ECR :latest (stale revision)")
                    except Exception as e:
                        out.append(f"  ECR :latest check: {e}")
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
            arm = any("g" in (t or "") for t in types) or "arm64" in str(types).lower()
            out.append(f"  CE instanceTypes: {types} -> ARM64={arm}")
            if not arm:
                out.append("  WARN: CE is x86; image from academy-build-arm64 is ARM64 -> mismatch")
                issues.append("Architecture mismatch: CE x86 vs image ARM64 (use c6g/m6g for ARM64)")
        except Exception as e:
            out.append(f"  Arch check: {e}")
    else:
        out.append("  Skipped (no CE or JobDef)")

    out.append("")
    out.append("--- ECR pull / container errors (CloudWatch) ---")
    pull_errors: list[str] = []
    try:
        streams = logs.describe_log_streams(
            logGroupName=LOG_GROUP,
            orderBy="LastEventTime",
            descending=True,
            limit=10,
        ).get("logStreams") or []
        for s in streams[:5]:
            name = s.get("logStreamName", "")
            events = logs.get_log_events(logGroupName=LOG_GROUP, logStreamName=name, limit=100).get("events") or []
            for ev in events:
                msg = (ev.get("message") or "").lower()
                if "cannotpullcontainererror" in msg or "manifest unknown" in msg or "no basic auth" in msg or "access denied" in msg or "exec format" in msg or "exec format error" in msg:
                    pull_errors.append(f"  [{name}] {(ev.get('message') or '')[:150]}")
        if pull_errors:
            for line in pull_errors[:5]:
                out.append(line)
            issues.append("ECR pull or arch error in CloudWatch logs (CannotPullContainerError/manifest/exec format)")
        else:
            out.append("  No recent ECR pull/arch errors in log streams (or log group empty).")
    except Exception as e:
        out.append(f"  Logs: {e}")

    out.append("")
    out.append("--- IAM (compute node) ---")
    out.append("  Batch CE instance role (academy-batch-ecs-instance-role) needs: ecr:GetAuthorizationToken, ecr:BatchGetImage, ecr:GetDownloadUrlForLayer, logs:CreateLogStream, logs:PutLogEvents.")
    out.append("  Apply: .\\scripts\\infra\\batch_attach_ecs_instance_role_policies.ps1")

    for line in out:
        print(line)

    if issues:
        root = issues[0] if len(issues) == 1 else "; ".join(issues[:3])
    else:
        root = "No obvious issue; check RUNNABLE statusReason and CE scaling. Ensure image pushed and JobDef points to :latest."

    print("")
    print("========== ROOT CAUSE ==========")
    print(root)
    print("")
    print("========== FIX PLAN ==========")
    if "RUNNABLE" in root or "desiredvCpus" in root or "scaling" in root:
        fix_plan.append("Ensure CE is ENABLED and maxvCpus >= 1; run batch_attach_ecs_instance_role_policies.ps1 if instance role missing ECR/logs.")
    if "ECR" in root or "image" in root.lower() or "stale" in root.lower():
        fix_plan.append("Push image: .\\scripts\\build_and_push_ecr_remote.ps1 -VideoWorkerOnly")
        fix_plan.append("Register JobDef revision: .\\scripts\\fix_and_redeploy_video_worker.ps1 (or batch_video_verify_and_register.ps1 with ECR URI).")
    if "Architecture" in root or "mismatch" in root:
        fix_plan.append("Use CE instanceTypes matching image: c6g/m6g for ARM64 (academy-build-arm64 builds ARM64).")
    if "IAM" in root or "AccessDenied" in root or "ECR pull" in root:
        fix_plan.append("Attach ECR + logs to academy-batch-ecs-instance-role: .\\scripts\\infra\\batch_attach_ecs_instance_role_policies.ps1")
    if "Queue" in root or "CE name" in root:
        fix_plan.append("Run batch_video_setup.ps1 with VpcId, SubnetIds, SecurityGroupId, EcrRepoUri.")
    if not fix_plan:
        fix_plan.append("Run .\\scripts\\fix_and_redeploy_video_worker.ps1 for full apply (IAM + JobDef + test job).")
    for b in fix_plan:
        print(f"  - {b}")
    print("")
    print("========== COMMANDS TO APPLY ==========")
    commands.append("aws batch describe-compute-environments --compute-environments " + (ce_name or "academy-video-batch-ce") + " --region " + REGION)
    commands.append("aws batch list-jobs --job-queue " + QUEUE_NAME + " --job-status RUNNABLE --region " + REGION)
    commands.append(".\\scripts\\infra\\batch_attach_ecs_instance_role_policies.ps1")
    commands.append(".\\scripts\\build_and_push_ecr_remote.ps1 -VideoWorkerOnly")
    commands.append(".\\scripts\\fix_and_redeploy_video_worker.ps1")
    for c in commands:
        print(f"  {c}")
    print("")


if __name__ == "__main__":
    _check_root_credentials()
    main()
