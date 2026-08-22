import json
from pathlib import Path
import re
import subprocess

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
PARAMS = REPO_ROOT / "docs" / "ssot" / "params.yaml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "v1-build-and-push-latest.yml"
ROOT_GUARD = REPO_ROOT / "scripts" / "v1" / "core" / "env.ps1"
DEPLOY = REPO_ROOT / "scripts" / "v1" / "deploy-api-development.ps1"
API_RESOURCE = REPO_ROOT / "scripts" / "v1" / "resources" / "api.ps1"
PREREQUISITES = (
    REPO_ROOT / "scripts" / "v1" / "converge-api-development-prerequisites.ps1"
)
PUBLISH = REPO_ROOT / "scripts" / "v1" / "publish-api-development-env.ps1"
INITIALIZE = REPO_ROOT / "scripts" / "v1" / "initialize-api-development.ps1"
REAL_USE_SMOKE = REPO_ROOT / "scripts" / "v1" / "run-api-development-smoke.ps1"
LOGIN_UAT_CLEANUP = (
    REPO_ROOT / "scripts" / "v1" / "destroy-ymath-login-uat-development.ps1"
)
LOGIN_UAT_CLEANUP_CONTRACT = (
    REPO_ROOT / "scripts" / "v1" / "core" / "ymath_login_uat.ps1"
)
SETTINGS = REPO_ROOT / "apps" / "api" / "config" / "settings" / "development.py"
WORKER_SETTINGS = REPO_ROOT / "apps" / "api" / "config" / "settings" / "worker.py"
IAM = REPO_ROOT / "scripts" / "v1" / "resources" / "iam.ps1"
OIDC_POLICY = (
    REPO_ROOT
    / "infra"
    / "worker_asg"
    / "iam_policy_gha_development_deploy.json"
)
OIDC_CONVERGE = (
    REPO_ROOT / "scripts" / "v1" / "converge-api-development-oidc.ps1"
)
DATABASE_CONVERGE = (
    REPO_ROOT / "scripts" / "v1" / "converge-api-preprod-database.ps1"
)
API_DOCKERFILE = REPO_ROOT / "docker" / "api" / "Dockerfile"


def _job_block(source: str, name: str) -> str:
    marker = f"  {name}:"
    block = source.split(marker, maxsplit=1)[1]
    next_job = re.search(r"\n  [a-zA-Z0-9_-]+:\n", block)
    return block if next_job is None else block[: next_job.start()]


def test_development_and_production_keep_tools_and_ai_workers_warm() -> None:
    params = yaml.safe_load(PARAMS.read_text(encoding="utf-8"))
    ai = params["aiWorker"]
    tools = params["toolsWorker"]
    deploy = DEPLOY.read_text(encoding="utf-8-sig")

    assert ai["instanceType"] == "t4g.medium"
    assert ai["minSize"] == 1
    assert ai["desiredCapacity"] == 1
    assert ai["maxSize"] == 5
    assert tools["instanceType"] == "t4g.small"
    assert tools["minSize"] == 1
    assert tools["desiredCapacity"] == 1
    assert tools["maxSize"] == 2
    assert "academy-tools-development" in deploy
    assert "academy-ai-development" in deploy
    assert "Development Tools worker stays a separate container/process" in deploy
    assert "Development AI worker stays a separate container/process" in deploy
    assert "AI_WORKER_IDLE_SCALE_IN_ENABLED=0" in deploy


def test_api_disables_unused_gunicorn_control_socket() -> None:
    dockerfile = API_DOCKERFILE.read_text(encoding="utf-8")

    assert "--no-control-socket" in dockerfile


def test_development_gate_runs_synthetic_excel_ppt_and_r2_review() -> None:
    deploy = DEPLOY.read_text(encoding="utf-8-sig")
    smoke = REAL_USE_SMOKE.read_text(encoding="utf-8-sig")

    assert 'Join-Path $ScriptRoot "run-api-development-smoke.ps1"' in deploy
    assert "-InstanceId $instanceId" in deploy
    assert deploy.index("run-api-development-smoke.ps1") < deploy.index(
        '"Key=Lifecycle,Value=active"'
    )
    assert "parse_student_excel_file" in smoke
    assert "PptComposer" in smoke
    assert "academy-ai-development" in smoke
    assert "R2_STORAGE_BUCKET.startswith(\"academy-development-\")" in smoke
    assert "put_object" in smoke
    assert "get_object" in smoke
    assert "delete_object" in smoke
    assert "worker_r2_output" in smoke
    assert "academy-api-asg" not in smoke
    assert "/academy/api/env" not in smoke


def test_login_uat_cleanup_reuses_exact_owned_development_instance_and_requires_zero_residue() -> None:
    source = LOGIN_UAT_CLEANUP.read_text(encoding="utf-8-sig")
    contract = LOGIN_UAT_CLEANUP_CONTRACT.read_text(encoding="utf-8-sig")

    assert "^qa-ymath-realuse-[a-z0-9-]+$" in source
    assert "ApiDevelopmentInstanceName" in source
    assert "ApiDevelopmentManagedByTag" in source
    assert '"--instance-ids", $InstanceId' in source
    assert '$tags["Lifecycle"] -ne "active"' in source
    assert '$tags["Environment"] -ne "development"' in source
    assert "setup_ymath_realuse_scenario --tenant-code '$TenantCode' --destroy" in source
    assert "YMATH_REALUSE_SCENARIO_DESTROYED" in contract
    assert "YMATH_REALUSE_SCENARIO_ABSENT" in contract
    assert "core\\ymath_login_uat.ps1" in source
    assert "Assert-YmathLoginUatCleanupPayload" in source
    assert "[int]$payload.remaining" not in source
    assert "Get-APIASGInstanceIds" not in source


def _run_cleanup_payload_contract(payload: dict) -> subprocess.CompletedProcess[str]:
    helper = str(LOGIN_UAT_CLEANUP_CONTRACT).replace("'", "''")
    command = (
        f". '{helper}'; "
        "$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json; "
        "Assert-YmathLoginUatCleanupPayload "
        "-Payload $payload -TenantCode 'qa-ymath-realuse-contract' | Out-Null"
    )
    return subprocess.run(
        ["pwsh", "-NoProfile", "-NonInteractive", "-Command", command],
        input=json.dumps(payload),
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )


def test_login_uat_cleanup_payload_rejects_missing_null_and_non_numeric_remaining() -> None:
    base = {
        "status": "YMATH_REALUSE_SCENARIO_ABSENT",
        "tenant_code": "qa-ymath-realuse-contract",
        "remaining": {"tenants": 0, "users": 0},
    }
    valid = _run_cleanup_payload_contract(base)
    assert valid.returncode == 0, valid.stderr

    invalid_payloads = (
        {key: value for key, value in base.items() if key != "remaining"},
        {**base, "remaining": {}},
        {**base, "remaining": None},
        {**base, "remaining": {"tenants": None, "users": 0}},
        {**base, "remaining": {"tenants": 0, "users": None}},
        {**base, "remaining": {"tenants": "0", "users": 0}},
        {**base, "remaining": {"tenants": 0, "users": "not-a-number"}},
    )
    for payload in invalid_payloads:
        rejected = _run_cleanup_payload_contract(payload)
        assert rejected.returncode != 0, payload


def test_development_ssot_is_isolated_and_matches_production_compute() -> None:
    params = yaml.safe_load(PARAMS.read_text(encoding="utf-8"))
    development = params["apiDevelopment"]

    assert development["enabled"] is True
    assert development["accessMode"] == "ssm-only"
    assert development["matchProductionCompute"] is True
    assert development["databaseName"] != "academy"
    assert "/development/" in development["ssmEnvParameter"]
    assert "/development/" in development["workersEnvParameter"]
    assert all(
        value.startswith("academy-v1-development-")
        for value in (
            development["aiQueueName"],
            development["toolsQueueName"],
            development["messagingQueueName"],
        )
    )
    assert development["r2CredentialParameter"] == (
        "/academy/r2/development/credentials"
    )
    assert development["r2BucketName"].startswith("academy-development-")


def test_isolated_database_role_owns_and_can_migrate_public_schema() -> None:
    source = DATABASE_CONVERGE.read_text(encoding="utf-8-sig")

    assert "ALTER SCHEMA public OWNER TO" in source
    assert "REVOKE ALL ON SCHEMA public FROM PUBLIC" in source
    assert "GRANT USAGE, CREATE ON SCHEMA public TO" in source
    assert "schema_owner != ROLE" in source
    assert "not schema_usage" in source
    assert "not schema_create" in source
    assert 'cursor.execute("CREATE EXTENSION IF NOT EXISTS vector")' in source
    assert "or not vector_extension_version" in source
    assert (
        '"ALTER ROLE {} WITH LOGIN NOCREATEDB NOCREATEROLE "'
        in source
    )
    assert (
        '"CREATE ROLE {} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "'
        in source
    )


def test_mutation_entrypoints_warn_on_explicitly_authorized_account_root() -> None:
    guard = ROOT_GUARD.read_text(encoding="utf-8-sig")
    deploy = DEPLOY.read_text(encoding="utf-8-sig")
    prerequisites = PREREQUISITES.read_text(encoding="utf-8-sig")

    assert "function Assert-AwsMutationIdentity" in guard
    assert "iam::[0-9]{12}:root" in guard
    assert "AWS account-root credential is active" in guard
    assert "all continuity gates" in guard
    assert "Assert-AwsMutationIdentity" in deploy
    assert "Assert-AwsMutationIdentity" in prerequisites


def test_development_settings_fail_closed_on_external_write_targets() -> None:
    settings = SETTINGS.read_text(encoding="utf-8")
    publish = PUBLISH.read_text(encoding="utf-8-sig")

    for token in (
        "ACADEMY_RUNTIME_ENV",
        "academy_api_development",
        "academy-v1-development-",
        "R2_ACCESS_KEY",
        "R2_SECRET_KEY",
        "r2.cloudflarestorage.com",
        "SOLAPI_MOCK",
        "TOSS_AUTO_BILLING_ENABLED",
        'TENANT_HEADER_NAME = "X-Tenant-Code"',
        "TENANT_DEFAULT_CODE = None",
    ):
        assert token in settings
    for token in (
        "ApiDevelopmentDatabaseName",
        "ApiDevelopmentAiQueueName",
        "ApiDevelopmentToolsQueueName",
        "ApiDevelopmentMessagingQueueName",
        "ApiDevelopmentR2CredentialParameter",
        "ApiDevelopmentR2BucketName",
        "R2_ENDPOINT = $r2Endpoint",
        'SOLAPI_MOCK = "true"',
        'TOSS_AUTO_BILLING_ENABLED = "false"',
    ):
        assert token in publish
    assert "ApiPreprod" not in publish
    assert "amazonaws.com" not in publish
    assert "s3api" not in PREREQUISITES.read_text(encoding="utf-8-sig")


def test_worker_settings_use_development_storage_bucket_from_env() -> None:
    worker = WORKER_SETTINGS.read_text(encoding="utf-8-sig")

    assert 'R2_REGION = os.getenv("R2_REGION", "auto")' in worker
    assert 'R2_STORAGE_BUCKET = os.getenv("R2_STORAGE_BUCKET", "academy-storage")' in worker
    assert 'R2_ADMIN_BUCKET = os.getenv("R2_ADMIN_BUCKET", "academy-admin")' in worker


def test_development_role_cannot_read_production_env_or_touch_prod_queues() -> None:
    source = IAM.read_text(encoding="utf-8-sig")
    block = source.split("function Ensure-ApiDevelopmentIAM {", maxsplit=1)[1].split(
        "function Legacy-GitHubActionsDeployIAM", maxsplit=1
    )[0]

    assert "$script:EcrToolsRepo" in block
    assert "$script:EcrAiRepo" in block
    assert "EcrToolsWorkerRepo" not in block
    assert "EcrToolsWorkerRepo" not in INITIALIZE.read_text(encoding="utf-8-sig")
    assert "/academy/api/development/env" in block
    assert "/academy/workers/development/env" in block
    assert "/academy/api/env" not in block
    assert "academy-v1-ai-queue\"" not in block
    assert "academy-v1-tools-queue\"" not in block
    assert "academy-v1-messaging-queue\"" not in block
    assert "AmazonSSMManagedInstanceCore" in block
    assert "AmazonEC2ContainerRegistryPowerUser" not in block
    assert '"s3:' not in block


def test_development_oidc_policy_is_separate_exact_and_main_only() -> None:
    policy = json.loads(OIDC_POLICY.read_text(encoding="utf-8"))
    by_sid = {statement["Sid"]: statement for statement in policy["Statement"]}
    converge = OIDC_CONVERGE.read_text(encoding="utf-8-sig")
    prerequisites = PREREQUISITES.read_text(encoding="utf-8-sig")
    params = yaml.safe_load(PARAMS.read_text(encoding="utf-8"))

    assert len(by_sid) == len(policy["Statement"])
    run_resources = by_sid["DevelopmentRunInstances"]["Resource"]
    assert any("security-group/sg-" in resource for resource in run_resources)
    assert sum("/subnet-" in resource for resource in run_resources) == 2
    assert by_sid["DevelopmentPassRole"]["Resource"].endswith(
        "role/academy-api-development-role"
    )
    assert by_sid["DevelopmentLifecycle"]["Condition"]["StringEquals"] == {
        "ec2:ResourceTag/Name": "academy-v1-api-development",
        "ec2:ResourceTag/Project": "academy",
        "ec2:ResourceTag/ManagedBy": "academy-api-development",
    }
    env_read = by_sid["DevelopmentEnvRead"]["Resource"]
    assert any(resource.endswith("parameter/academy/api/env") for resource in env_read)
    assert all("preprod" not in resource for resource in env_read)
    assert "autoscaling:" not in OIDC_POLICY.read_text(encoding="utf-8")
    assert "Assert-AwsMutationIdentity" in converge
    assert "refs/heads/main" in converge
    assert (
        '$policyName = [string]$script:GitHubActionsDevelopmentDeployPolicyName'
        in converge
    )
    assert (
        params["githubActions"]["developmentDeployPolicyName"]
        == "academy-gha-development-deploy"
    )
    assert "converge-api-development-oidc.ps1" in prerequisites


def test_blue_green_development_deploy_preserves_old_instance_on_failure() -> None:
    source = DEPLOY.read_text(encoding="utf-8-sig")
    api_resource = API_RESOURCE.read_text(encoding="utf-8-sig")

    assert "match the production compute contract" in source
    assert "ApiDevelopmentSecurityGroupName" in source
    assert "IpPermissions" in source
    assert "Lifecycle,Value=candidate" in source
    assert "$promoted = $false" in source
    assert "if ($instanceId -and -not $promoted)" in source
    assert source.index("$promoted = $true") < source.index(
        "terminate prior API development instance"
    )
    assert "Value=true" in source
    assert "instance-initiated-shutdown-behavior\", \"stop\"" in source
    assert (
        "HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=2"
        in source
    )
    assert "DEVELOPMENT_BOUNDARY_PASS" in source
    assert "DEVELOPMENT_RUNTIME_PASS" in source
    assert "__AI_IMAGE__" in source
    assert "academy-ai-development" in source
    assert "start-instance-refresh" not in source
    assert "register-targets" not in source
    assert "academy-v1-api-asg" not in source
    assert "ApiDevelopmentEnvParameter" in source
    assert "ConvertTo-Json `\n    -InputObject $networkInterfacePayload" in source
    assert (
        '-ExpectedSettingsModule "apps.api.config.settings.development"'
        in source
    )
    assert (
        '[string]$ExpectedSettingsModule = "apps.api.config.settings.prod"'
        in api_resource
    )
    assert "expected='$ExpectedSettingsModule'" in api_resource


def test_workflow_enforces_development_then_preprod_then_production() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    development = _job_block(workflow, "verify-api-development")
    preprod = _job_block(workflow, "verify-api-preprod")
    migrations = _job_block(workflow, "run-migrations")
    production_api = _job_block(workflow, "deploy-api")

    assert "build-and-push" in development
    assert "publish-api-development-env.ps1" in development
    assert "deploy-api-development.ps1" in development
    assert "verify-api-development" in preprod
    assert "verify-api-preprod" in migrations
    assert "verify-api-preprod" in production_api
    assert workflow.index("  verify-api-development:") < workflow.index(
        "  verify-api-preprod:"
    )
    assert workflow.index("  verify-api-preprod:") < workflow.index(
        "  run-migrations:"
    )
