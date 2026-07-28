import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CANARY = REPO_ROOT / "scripts" / "v1" / "run-api-preprod-canary.ps1"
DEPLOY = REPO_ROOT / "scripts" / "v1" / "deploy.ps1"
API_RESOURCE = REPO_ROOT / "scripts" / "v1" / "resources" / "api.ps1"
SYNC_ENV = REPO_ROOT / "scripts" / "v1" / "core" / "sync_env.ps1"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "v1-build-and-push-latest.yml"
IAM = REPO_ROOT / "scripts" / "v1" / "resources" / "iam.ps1"


def test_manual_deploy_prepares_and_tests_env_before_live_promotion() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8-sig")

    prepare = deploy.index("Invoke-SyncEnvFromSSOT -PrepareOnly")
    candidate_parameter = deploy.index("Publish-ApiPreprodEnvCandidate")
    canary = deploy.index("run-api-preprod-canary.ps1")
    promote = deploy.index("Publish-RuntimeEnvCandidates", canary)
    production = deploy.index("Ensure-API", canary)

    assert prepare < candidate_parameter < canary < promote < production
    assert "Invoke-RefreshApiEnvOnInstances" not in deploy


def test_ci_runs_common_canary_before_migrations_and_every_deploy() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8-sig")
    canary_job = workflow.split("  verify-api-preprod:", maxsplit=1)[1].split(
        "  run-migrations:", maxsplit=1
    )[0]
    deploy_job = workflow.split("  deploy-api:", maxsplit=1)[1].split(
        "  deploy-messaging:", maxsplit=1
    )[0]

    assert "run-api-preprod-canary.ps1" in canary_job
    assert "release-manifest.candidate.json" in canary_job
    assert workflow.index("  verify-api-preprod:") < workflow.index(
        "  run-migrations:"
    ) < workflow.index("  deploy-api:")

    pin = deploy_job.index("pin-asg-image.ps1")
    refresh = deploy_job.index("start-instance-refresh")
    assert pin < refresh
    assert "continue-on-error: true  # IAM 권한 없으면 skip하고 instance refresh는 진행" not in deploy_job
    assert "Refusing production refresh" in deploy_job
    for job_name in (
        "deploy-api",
        "deploy-messaging",
        "deploy-ai",
        "deploy-tools",
        "deploy-video",
    ):
        match = re.search(
            rf"(?ms)^  {re.escape(job_name)}:.*?(?=^  [a-z0-9-]+:|\Z)",
            workflow,
        )
        assert match is not None
        block = match.group(0)
        assert "verify-api-preprod" in block


def test_canary_is_isolated_and_checks_migrations_and_health() -> None:
    canary = CANARY.read_text(encoding="utf-8-sig")

    assert "ec2\", \"run-instances" in canary
    assert "academy-v1-api-preprod-canary" in canary
    assert "not attached to ASG/ALB" in canary
    assert "/academy/api/preprod/env" in canary
    assert "academy-api-preprod-canary-role" in canary
    assert "academy-api-preprod-canary" in canary
    assert "academy_api_preprod" in canary
    assert "apps.api.config.settings.prod" in canary
    assert "python manage.py migrate --noinput" in canary
    assert "CREATE DATABASE" in canary
    assert "http://127.0.0.1:8000/healthz" in canary
    assert "http://127.0.0.1:8000/health" in canary
    assert "API_PREPROD_CANARY_PASS" in canary
    assert "ec2\", \"terminate-instances" in canary
    assert "ec2\", \"wait\", \"instance-terminated" in canary
    assert "shutdown -h +30" in canary
    assert "\"ssm\", \"get-parameter\"" not in canary
    assert canary.index("shutdown -h +30") < canary.index("docker run --rm")
    assert "$script:ApiAmiId" in canary
    assert "$script:ApiInstanceType" in canary
    assert "$script:ApiSecurityGroupId" in canary


def test_canary_proves_signed_cdn_master_variant_and_segment_chain() -> None:
    canary = CANARY.read_text(encoding="utf-8-sig")

    assert 'Prefix="tenants/"' in canary
    assert 'key.endswith("/master.m3u8")' in canary
    assert "CloudflareSignedURL" in canary
    assert "settings.CDN_HLS_SIGNING_SECRET" in canary
    assert "settings.CDN_HLS_SIGNING_KEY_ID" in canary
    assert "settings.CDN_HLS_BASE_URL" in canary
    assert "master_status" in canary
    assert "variant_status" in canary
    assert "segment_status" in canary
    assert '"Range"] = "bytes=0-1023"' in canary
    assert "CDN_PLAYBACK_CHAIN_PASS" in canary
    assert 'if ($proof -notmatch "CDN_PLAYBACK_CHAIN_PASS")' in canary


def test_api_env_sync_fails_closed_on_cross_role_or_missing_source() -> None:
    sync = SYNC_ENV.read_text(encoding="utf-8-sig")

    assert "Refusing to synthesize it from workers env" in sync
    assert (
        'Assert-RuntimeEnvSettingsModule -EnvObject $obj -Expected '
        '"apps.api.config.settings.prod"'
    ) in sync
    assert (
        'Assert-RuntimeEnvSettingsModule -EnvObject $obj -Expected '
        '"apps.api.config.settings.worker"'
    ) in sync
    assert "API env candidate prepared without mutating" in sync
    assert "Publish-ApiPreprodEnvCandidate" in sync
    assert "Publish-RuntimeEnvCandidates" in sync
    assert "restoring prior parameter values" in sync


def test_api_boot_and_env_refresh_require_prod_settings_and_real_health() -> None:
    api_resource = API_RESOURCE.read_text(encoding="utf-8-sig")
    refresh = (
        REPO_ROOT / "scripts" / "v1" / "inline" / "refresh-api-env.sh"
    ).read_text(encoding="utf-8")

    assert "API env fetch/validation failed after retries" in api_resource
    assert "apps.api.config.settings.prod" in api_resource
    assert "throw \"API health 200 timeout after production mutation" in api_resource
    assert "set -euo pipefail" in refresh
    assert "apps.api.config.settings.prod" in refresh
    assert "API_ENV_REFRESH_PASS healthz=200 health=200" in refresh
    assert "API_ENV_REFRESH_ROLLBACK_PASS previous container restored" in refresh
    assert "API_ENV_REFRESH_ROLLBACK_FAILED" in refresh
    assert "exit 70" in refresh


def test_canary_roles_and_cleanup_are_least_privilege() -> None:
    iam = IAM.read_text(encoding="utf-8-sig")

    assert "Ensure-ApiPreprodCanaryIAM" in iam
    assert "academy-api-preprod-canary-role" in iam
    assert "parameter/academy/api/preprod/env" in iam
    assert "AmazonSSMManagedInstanceCore" in iam
    assert 'Sid="ApiCanaryInstanceRead"' in iam
    assert 'Sid="ApiCanaryInstanceCleanup"' in iam
    assert 'Sid="ApiCanaryProfileRead"' in iam
    assert 'Sid="ApiCanarySsmRead"' in iam
    assert 'Sid="SsmSendApiCanary"' in iam
    assert '"ssm:DescribeInstanceInformation"' in iam
    assert '"ec2:ResourceTag/Name" = $apiCanaryInstanceTag' in iam
    assert '"ec2:ResourceTag/Project" = "academy"' in iam
    assert '"ec2:ResourceTag/ManagedBy" = "academy-deploy-canary"' in iam
    assert '$apiCanaryInstanceTag = "academy-v1-api-preprod-canary"' in iam
