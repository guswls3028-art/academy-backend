#!/usr/bin/env python3
"""
One-take verification for Video Delete -> AWS Batch TerminateJob.

Run on API server (EC2) or same IAM/credentials. Prints PASS/FAIL for TerminateJob.

Example commands:
  python scripts/verify_batch_terminate.py
  python scripts/verify_batch_terminate.py --job-id <AWS_BATCH_JOB_ID>
  python scripts/verify_batch_terminate.py --region ap-northeast-2
  python scripts/verify_batch_terminate.py --settings-module apps.api.config.settings.base

Exit codes: 0=PASS, 2=FAIL(permission), 3=FAIL(network/config), 4=WARN/SKIPPED.

If FAIL: IAM policy is in infra/worker_asg/iam_policy_api_batch_submit.json;
apply via scripts/apply_api_batch_submit_policy.ps1 (add batch:TerminateJob to Action).
"""
from __future__ import annotations

import argparse
import os
import sys

# ---------------------------------------------------------------------------
# Region resolution (Django optional)
# ---------------------------------------------------------------------------
def _get_region_from_args(args: argparse.Namespace) -> str:
    if getattr(args, "region", None):
        return args.region
    settings_module = getattr(args, "settings_module", None) or os.environ.get("DJANGO_SETTINGS_MODULE")
    if settings_module:
        try:
            import django
            if not django.apps.apps.ready:
                django.setup()
            from django.conf import settings
            r = getattr(settings, "AWS_REGION", None) or getattr(settings, "AWS_DEFAULT_REGION", None)
            if r:
                return r
        except Exception:
            pass
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-northeast-2"


def _create_session(profile: str | None):
    import boto3
    if profile:
        return boto3.Session(profile_name=profile)
    return boto3.Session()


def _get_credential_source() -> str:
    if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_SECRET_ACCESS_KEY"):
        return "environment (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY)"
    if os.environ.get("AWS_PROFILE"):
        return f"environment (AWS_PROFILE={os.environ.get('AWS_PROFILE')})"
    try:
        import urllib.request
        req = urllib.request.Request("http://169.254.169.254/latest/meta-data/", method="HEAD")
        urllib.request.urlopen(req, timeout=1)
        return "EC2 instance metadata (instance profile)"
    except Exception:
        pass
    return "default credential chain (best-effort)"


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Batch TerminateJob permission for Video Delete flow.")
    parser.add_argument("--region", default=None, help="AWS region (default: settings/env/ap-northeast-2)")
    parser.add_argument("--job-id", default=None, help='Optional AWS Batch job ID for real DescribeJobs + TerminateJob probe (use quotes in PowerShell: -JobId "job-id-here")')
    parser.add_argument("--profile", default=None, help="Optional AWS profile name")
    parser.add_argument("--settings-module", default=None, help="Optional Django settings module for region default")
    args = parser.parse_args()

    region = _get_region_from_args(args)
    session = _create_session(args.profile)

    print("=== Video Delete -> Batch TerminateJob Verification ===\n")

    # --- Caller identity ---
    try:
        sts = session.client("sts", region_name=region)
        ident = sts.get_caller_identity()
        account = ident.get("Account", "")
        arn = ident.get("Arn", "")
        user_id = ident.get("UserId", "")
        print(f"Caller identity:")
        print(f"  Account: {account}")
        print(f"  ARN:     {arn}")
        print(f"  UserId:  {user_id}")
    except Exception as e:
        print(f"FAIL: Could not get caller identity: {e}")
        return 3

    print(f"\nCredential source: {_get_credential_source()}")
    print(f"Region:            {region}")
    print("Operator note:     IAM policy = infra/worker_asg/iam_policy_api_batch_submit.json | Apply = scripts/apply_api_batch_submit_policy.ps1\n")

    # For SimulatePrincipalPolicy we need role ARN when caller is assumed-role (e.g. EC2 instance profile).
    # Root account ARN (arn:aws:iam::account:root) is not supported by SimulatePrincipalPolicy.
    policy_source_arn = arn
    if ":assumed-role/" in arn:
        # arn:aws:sts::123456789:assumed-role/academy-ec2-role/i-xxx -> arn:aws:iam::123456789:role/academy-ec2-role
        parts = arn.split(":assumed-role/", 1)
        if len(parts) == 2:
            prefix = parts[0]  # arn:aws:sts
            rest = parts[1]    # role-name/session
            if "/" in rest:
                role_name = rest.split("/", 1)[0]
            else:
                role_name = rest
            # sts -> iam, assumed-role -> role
            policy_source_arn = f"arn:aws:iam::{account}:role/{role_name}"
            print(f"PolicySourceArn for simulation: {policy_source_arn}\n")

    sim_allowed: bool | None = None  # True = allowed, False = denied, None = skipped
    batch_access_denied: bool = False
    batch_network_error: bool = False
    terminate_succeeded: bool = False

    # --- 1) SimulatePrincipalPolicy ---
    print("--- 1) IAM SimulatePrincipalPolicy ---")
    if policy_source_arn.rstrip("/").endswith(":root"):
        print("  SKIPPED (SimulatePrincipalPolicy does not support root account ARN; use --job-id for API probe or run with IAM role credentials).")
    else:
        try:
            iam = session.client("iam", region_name=region)
            result = iam.simulate_principal_policy(
                PolicySourceArn=policy_source_arn,
                ActionNames=["batch:TerminateJob", "batch:DescribeJobs"],
            )
            results = result.get("EvaluationResults", [])
            term_result = next((r for r in results if r.get("EvalActionName") == "batch:TerminateJob"), None)
            desc_result = next((r for r in results if r.get("EvalActionName") == "batch:DescribeJobs"), None)
            if term_result:
                effect = term_result.get("EvalDecision")  # "allowed" | "explicitDeny"
                sim_allowed = effect == "allowed"
                print(f"  batch:TerminateJob  -> {effect}")
            else:
                print("  batch:TerminateJob  -> (no result)")
            if desc_result:
                print(f"  batch:DescribeJobs  -> {desc_result.get('EvalDecision', 'N/A')}")
            else:
                print("  batch:DescribeJobs  -> (no result)")
        except Exception as e:
            err_str = str(e).lower()
            if "accessdenied" in err_str or "not authorized" in err_str:
                print("  SKIPPED (caller lacks iam:SimulatePrincipalPolicy)")
                sim_allowed = None
            elif "invalidinput" in err_str or "invalid arn" in err_str:
                print("  SKIPPED (SimulatePrincipalPolicy does not support this principal ARN; use --job-id for API probe).")
                sim_allowed = None
            else:
                print(f"  SKIPPED (error): {e}")
                sim_allowed = None

    # --- 2) API-level probe ---
    print("\n--- 2) API-level permission probe ---")
    batch = session.client("batch", region_name=region)

    if args.job_id:
        job_id = args.job_id.strip()
        try:
            batch.describe_jobs(jobs=[job_id])
            print(f"  batch:DescribeJobs(jobId={job_id}) -> OK")
        except Exception as e:
            from botocore.exceptions import ClientError
            if isinstance(e, ClientError):
                code = e.response.get("Error", {}).get("Code", "")
                if code == "AccessDeniedException" or "AccessDenied" in str(e):
                    print(f"  batch:DescribeJobs -> AccessDenied")
                    batch_access_denied = True
                else:
                    print(f"  batch:DescribeJobs -> {code}: {e}")
            else:
                print(f"  batch:DescribeJobs -> {e}")
                batch_network_error = True

        if not batch_access_denied and not batch_network_error:
            try:
                batch.terminate_job(jobId=job_id, reason="verify_terminate_permission")
                print(f"  batch:TerminateJob(jobId={job_id}) -> OK")
                terminate_succeeded = True
            except Exception as e:
                from botocore.exceptions import ClientError
                if isinstance(e, ClientError):
                    code = e.response.get("Error", {}).get("Code", "")
                    if code == "AccessDeniedException" or "AccessDenied" in str(e):
                        print(f"  batch:TerminateJob -> AccessDenied")
                        batch_access_denied = True
                    else:
                        print(f"  batch:TerminateJob -> {code}: {e}")
                else:
                    print(f"  batch:TerminateJob -> {e}")
                    batch_network_error = True
    else:
        try:
            batch.describe_job_queues()
            print("  batch:DescribeJobQueues -> OK (harmless probe)")
        except Exception as e:
            from botocore.exceptions import ClientError
            if isinstance(e, ClientError):
                code = e.response.get("Error", {}).get("Code", "")
                if code == "AccessDeniedException" or "AccessDenied" in str(e):
                    print("  batch:DescribeJobQueues -> AccessDenied")
                    batch_access_denied = True
                else:
                    print(f"  batch:DescribeJobQueues -> {code}: {e}")
            else:
                print(f"  batch:DescribeJobQueues -> {e}")
                batch_network_error = True

    # --- Result ---
    print("\n=== Result ===")
    if batch_network_error:
        print("WARN: Network/endpoint/region error; could not conclude TerminateJob permission.")
        print("Exit code: 3 (config/network)")
        return 3
    if batch_access_denied:
        print("FAIL: AccessDenied for a required Batch action (TerminateJob or DescribeJobs).")
        print("Fix: Add batch:TerminateJob to infra/worker_asg/iam_policy_api_batch_submit.json")
        print("     Then run: scripts/apply_api_batch_submit_policy.ps1")
        print("Exit code: 2 (permission)")
        return 2
    if terminate_succeeded:
        print("PASS: TerminateJob succeeded (real job probe).")
        print("Exit code: 0 (PASS)")
        return 0
    if sim_allowed is True:
        print("PASS: SimulatePrincipalPolicy allows batch:TerminateJob.")
        print("Exit code: 0 (PASS)")
        return 0
    if sim_allowed is False:
        print("FAIL: SimulatePrincipalPolicy denies batch:TerminateJob.")
        print("Fix: Add batch:TerminateJob to infra/worker_asg/iam_policy_api_batch_submit.json")
        print("     Then run: scripts/apply_api_batch_submit_policy.ps1")
        print("Exit code: 2 (permission)")
        return 2
    # sim_allowed is None and no job-id or no terminate call
    print("WARN/SKIPPED: Could not conclusively verify TerminateJob (no --job-id, SimulatePrincipalPolicy skipped).")
    print('Run with --job-id "YOUR_AWS_BATCH_JOB_ID" for a real TerminateJob probe (PowerShell: -JobId "YOUR_AWS_BATCH_JOB_ID").')
    print("Exit code: 4 (WARN/SKIPPED)")
    return 4


if __name__ == "__main__":
    sys.exit(main())
