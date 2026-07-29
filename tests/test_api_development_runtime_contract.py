import json
from pathlib import Path
import re

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
SETTINGS = REPO_ROOT / "apps" / "api" / "config" / "settings" / "development.py"
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


def _job_block(source: str, name: str) -> str:
    marker = f"  {name}:"
    block = source.split(marker, maxsplit=1)[1]
    next_job = re.search(r"\n  [a-zA-Z0-9_-]+:\n", block)
    return block if next_job is None else block[: next_job.start()]


def test_development_host_keeps_tools_worker_warm_without_production_capacity_change() -> None:
    params = yaml.safe_load(PARAMS.read_text(encoding="utf-8"))
    tools = params["toolsWorker"]
    deploy = DEPLOY.read_text(encoding="utf-8-sig")

    assert tools["instanceType"] == "t4g.small"
    assert tools["minSize"] == 0
    assert tools["desiredCapacity"] == 0
    assert tools["maxSize"] == 2
    assert "academy-tools-development" in deploy
    assert "Development Tools worker stays a separate container/process" in deploy


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


def test_mutation_entrypoints_reject_account_root_credentials() -> None:
    guard = ROOT_GUARD.read_text(encoding="utf-8-sig")
    deploy = DEPLOY.read_text(encoding="utf-8-sig")
    prerequisites = PREREQUISITES.read_text(encoding="utf-8-sig")

    assert "function Assert-AwsMutationIdentity" in guard
    assert "iam::[0-9]{12}:root" in guard
    assert "AWS mutation is blocked for account root credentials" in guard
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


def test_development_role_cannot_read_production_env_or_touch_prod_queues() -> None:
    source = IAM.read_text(encoding="utf-8-sig")
    block = source.split("function Ensure-ApiDevelopmentIAM {", maxsplit=1)[1].split(
        "function Legacy-GitHubActionsDeployIAM", maxsplit=1
    )[0]

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
    assert "academy-gha-development-deploy" in converge
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
