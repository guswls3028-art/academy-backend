# Launch one isolated API instance with the candidate image, dedicated IAM,
# candidate-only SSM env, and non-production database. The instance is never
# attached to the production ASG or ALB.
[CmdletBinding()]
param(
    [ValidatePattern('^sha-(?:[0-9a-fA-F]{8,40}|[0-9a-fA-F]{40}-run-[0-9]+-[0-9]+)$')]
    [string]$ImageTag = "",
    [ValidatePattern('^[0-9]+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/academy-api@sha256:[0-9a-fA-F]{64}$')]
    [string]$ImageUri = "",
    [ValidateRange(300, 1800)]
    [int]$TimeoutSec = 900,
    [ValidatePattern('^/academy/api/preprod/env$')]
    [string]$SsmApiEnvParameter = "/academy/api/preprod/env",
    [ValidatePattern('^[a-z][a-z0-9_]{2,62}$')]
    [string]$ExpectedDatabaseName = "academy_api_preprod",
    [ValidatePattern('^[A-Za-z0-9+=,.@_-]{3,128}$')]
    [string]$CanaryInstanceProfileName = "academy-api-preprod-canary",
    [switch]$Ci = $false,
    [string]$AwsProfile = "default"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot

if ($Ci) {
    Remove-Item Env:AWS_PROFILE -ErrorAction SilentlyContinue
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
} elseif ($AwsProfile -and $AwsProfile.Trim()) {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }
}

$script:PlanMode = $false
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\logging.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
. (Join-Path $ScriptRoot "core\wait.ps1")
. (Join-Path $ScriptRoot "core\sync_env.ps1")
. (Join-Path $ScriptRoot "resources\worker_userdata.ps1")
. (Join-Path $ScriptRoot "resources\api.ps1")

Load-SSOT -Env prod | Out-Null
if (-not $script:EcrImmutableTagRequired -or $script:EcrUseLatestTag) {
    throw "API pre-production canary requires immutable digest-pinned images."
}
if (-not $ImageUri) {
    if (-not $ImageTag) { throw "ImageTag or ImageUri is required." }
    $ImageUri = Get-ImmutableEcrImageUri -RepoName $script:EcrApiRepo -ImageTag $ImageTag.ToLowerInvariant()
}
Assert-ImmutableEcrImageUri -ImageUri $ImageUri

$asgResult = Invoke-AwsJson @(
    "autoscaling", "describe-auto-scaling-groups",
    "--auto-scaling-group-names", $script:ApiASGName,
    "--region", $script:Region,
    "--output", "json"
)
$apiAsg = @($asgResult.AutoScalingGroups)[0]
$launchTemplate = $apiAsg.LaunchTemplate
if (-not $apiAsg -or -not $launchTemplate -or -not $launchTemplate.LaunchTemplateId) {
    throw "API ASG or Launch Template not found: $($script:ApiASGName)"
}
$launchTemplateVersion = if ($launchTemplate.Version) { [string]$launchTemplate.Version } else { '$Latest' }
$versionResult = Invoke-AwsJson @(
    "ec2", "describe-launch-template-versions",
    "--launch-template-id", $launchTemplate.LaunchTemplateId,
    "--versions", $launchTemplateVersion,
    "--region", $script:Region,
    "--output", "json"
)
$version = @($versionResult.LaunchTemplateVersions)[0]
if (-not $version) { throw "Production API Launch Template version not found." }
$data = $version.LaunchTemplateData
$ami = if ($script:ApiAmiId) { [string]$script:ApiAmiId } else { [string]$data.ImageId }
$instanceType = if ($script:ApiInstanceType) { [string]$script:ApiInstanceType } else { [string]$data.InstanceType }
$profileName = $CanaryInstanceProfileName
$securityGroups = if ($script:ApiSecurityGroupId) {
    @([string]$script:ApiSecurityGroupId)
} else {
    @($data.SecurityGroupIds | Where-Object { $_ })
}
$subnets = if ($script:ApiSubnetId) { @([string]$script:ApiSubnetId) } else { @() }
if (-not $subnets -or $subnets.Count -eq 0) {
    $subnets = if (-not $script:NatEnabled) {
        @($script:PublicSubnets | Where-Object { $_ })
    } else {
        @($script:PrivateSubnets | Where-Object { $_ })
    }
}
if (-not $subnets -or $subnets.Count -eq 0) {
    $subnets = @(([string]$apiAsg.VPCZoneIdentifier -split ",") | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}
$subnet = [string]$subnets[0]
if (-not $ami -or -not $instanceType -or -not $profileName -or $securityGroups.Count -eq 0 -or -not $subnet) {
    throw "API canary could not resolve desired AMI, instance type, dedicated profile, security group, and subnet."
}
$profile = Invoke-AwsJson @(
    "iam", "get-instance-profile",
    "--instance-profile-name", $profileName,
    "--output", "json"
)
$profileRoles = @($profile.InstanceProfile.Roles | Where-Object { $_.RoleName })
if ($profileRoles.Count -ne 1 -or [string]$profileRoles[0].RoleName -ne "academy-api-preprod-canary-role") {
    throw "API canary instance profile is not bound exclusively to the dedicated canary role."
}

$deploymentId = if ($ImageTag) { $ImageTag } else { ($ImageUri -split '@')[-1] }
$userData = Get-ApiLaunchTemplateUserData `
    -ApiImageUri $ImageUri `
    -Region $script:Region `
    -SsmApiEnvParam $SsmApiEnvParameter `
    -DeploymentId "preprod-$deploymentId"
if (-not $userData) { throw "API canary userdata rendering failed." }
# Schedule cleanup before any fallible bootstrap command. With
# instance-initiated-shutdown-behavior=terminate, a cancelled runner cannot
# leave an orphaned canary.
$userData = [regex]::Replace(
    $userData,
    '(?m)^set -e\s*$',
    "set -euo pipefail`nshutdown -h +30 >/var/log/academy-api-canary-shutdown.log 2>&1 || true",
    1
)
if ($userData -notmatch 'shutdown -h \+30') { throw "API canary shutdown backstop injection failed." }
$dbBootstrap = @'
# Preprod-only database: create once, then validate candidate migrations before
# starting the server. No production database schema is changed in this phase.
CANARY_IMAGE="__CANARY_IMAGE__"
docker run --rm --env-file /opt/api.env "$CANARY_IMAGE" python -c "import os, psycopg2; from psycopg2 import sql; conn=psycopg2.connect(dbname='postgres', user=os.environ['DB_USER'], password=os.environ['DB_PASSWORD'], host=os.environ['DB_HOST'], port=os.environ.get('DB_PORT','5432'), sslmode=os.environ.get('DB_SSL_MODE','require')); conn.set_session(autocommit=True); cur=conn.cursor(); cur.execute('SELECT 1 FROM pg_database WHERE datname=%s',(os.environ['DB_NAME'],)); exists=cur.fetchone(); cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(os.environ['DB_NAME']))) if not exists else None; cur.close(); conn.close()"
docker run --rm --env-file /opt/api.env "$CANARY_IMAGE" python manage.py migrate --noinput
'@
$dbBootstrap = $dbBootstrap.Replace("__CANARY_IMAGE__", $ImageUri)
$userData = [regex]::Replace($userData, '(?m)^# 4\)', "$dbBootstrap`n# 4)", 1)

$canaryName = "academy-v1-api-preprod-canary"
$tagSpec = "ResourceType=instance,Tags=[{Key=Name,Value=$canaryName},{Key=Project,Value=academy},{Key=ManagedBy,Value=academy-deploy-canary}]"
$runArgs = @(
    "ec2", "run-instances",
    "--image-id", $ami,
    "--instance-type", $instanceType,
    "--iam-instance-profile", "Name=$profileName",
    "--security-group-ids"
) + $securityGroups + @(
    "--subnet-id", $subnet,
    "--user-data", $userData,
    "--metadata-options", "HttpTokens=required,HttpEndpoint=enabled",
    "--instance-initiated-shutdown-behavior", "terminate",
    "--tag-specifications", $tagSpec,
    "--count", "1",
    "--region", $script:Region,
    "--output", "json"
)

$instanceId = ""
$cleanupFailure = $null
try {
    Write-Host "Launching isolated API pre-production canary (not attached to ASG/ALB)..." -ForegroundColor Cyan
    $runRaw = Invoke-Aws $runArgs -ErrorMessage "launch API pre-production canary"
    $run = ($runRaw | Out-String).Trim() | ConvertFrom-Json
    $instanceId = [string]@($run.Instances)[0].InstanceId
    if (-not $instanceId) { throw "API canary launch returned no instance id." }
    Write-Host "  Canary instance: $instanceId" -ForegroundColor DarkGray

    Invoke-Aws @(
        "ec2", "wait", "instance-running",
        "--instance-ids", $instanceId,
        "--region", $script:Region
    ) -ErrorMessage "wait for API canary instance-running" | Out-Null
    Wait-SSMOnline -InstanceId $instanceId -Reg $script:Region -TimeoutSec $TimeoutSec

$remote = @'
set -euo pipefail
container=academy-api
for i in $(seq 1 60); do
  state=$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || true)
  if [ "$state" = "running" ]; then break; fi
  sleep 5
done
state=$(docker inspect -f '{{.State.Status}}' "$container" 2>/dev/null || true)
if [ "$state" != "running" ]; then
  echo "CANARY_FAIL container_state=$state" >&2
  tail -n 120 /var/log/academy-api-userdata.log >&2 || true
  exit 40
fi
settings_module=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$container" | sed -n 's/^DJANGO_SETTINGS_MODULE=//p')
if [ "$settings_module" != "apps.api.config.settings.prod" ]; then
  echo "CANARY_FAIL settings_module=$settings_module" >&2
  exit 41
fi
database_name=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$container" | sed -n 's/^DB_NAME=//p')
if [ "$database_name" != "__EXPECTED_DATABASE__" ]; then
  echo "CANARY_FAIL database_boundary" >&2
  exit 42
fi
healthz=000
health=000
for i in $(seq 1 45); do
  healthz=$(curl -sS -o /tmp/canary-healthz.out -w '%{http_code}' --max-time 3 http://127.0.0.1:8000/healthz || true)
  health=$(curl -sS -o /tmp/canary-health.out -w '%{http_code}' --max-time 3 http://127.0.0.1:8000/health || true)
  if [ "$healthz" = "200" ] && [ "$health" = "200" ]; then break; fi
  sleep 4
done
if [ "$healthz" != "200" ] || [ "$health" != "200" ]; then
  echo "CANARY_FAIL healthz=$healthz health=$health" >&2
  docker logs --tail 120 "$container" >&2 || true
  exit 43
fi
image=$(docker inspect -f '{{.Config.Image}}' "$container")
if [ "$image" != "__EXPECTED_IMAGE__" ]; then
  echo "CANARY_FAIL image_mismatch" >&2
  exit 44
fi

# Prove the candidate's R2 and CDN signing configuration against a real,
# read-only HLS object before any production env or ASG mutation.
if ! video_chain_proof=$(docker exec -i "$container" python <<'PY'
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen

import boto3
from django.conf import settings

from apps.domains.video.cdn.cloudflare_signing import CloudflareSignedURL

required = (
    "R2_ENDPOINT",
    "R2_ACCESS_KEY",
    "R2_SECRET_KEY",
    "R2_VIDEO_BUCKET",
)
missing = [name for name in required if not os.environ.get(name, "").strip()]
if missing:
    raise SystemExit("missing video storage env: " + ",".join(missing))

client = boto3.client(
    "s3",
    endpoint_url=os.environ["R2_ENDPOINT"],
    aws_access_key_id=os.environ["R2_ACCESS_KEY"],
    aws_secret_access_key=os.environ["R2_SECRET_KEY"],
    region_name="auto",
)
paginator = client.get_paginator("list_objects_v2")
master_keys = []
for page_number, page in enumerate(
    paginator.paginate(
        Bucket=os.environ["R2_VIDEO_BUCKET"],
        Prefix="tenants/",
        PaginationConfig={"PageSize": 1000, "MaxItems": 10000},
    ),
    start=1,
):
    for item in page.get("Contents", []):
        key = str(item.get("Key", ""))
        if key.endswith("/master.m3u8") and "/_tmp/" not in key:
            master_keys.append(key)
            if len(master_keys) >= 20:
                break
    if len(master_keys) >= 20 or page_number >= 10:
        break
if not master_keys:
    raise SystemExit("no stable HLS master object found for CDN canary")

signer = CloudflareSignedURL(
    secret=settings.CDN_HLS_SIGNING_SECRET,
    key_id=settings.CDN_HLS_SIGNING_KEY_ID,
)


def fetch(url, *, range_request=False):
    headers = {"User-Agent": "Academy-API-Preprod-Canary/1.0"}
    if range_request:
        headers["Range"] = "bytes=0-1023"
    with urlopen(Request(url, headers=headers), timeout=15) as response:
        return response.status, response.read()


def first_media_url(body, parent_url):
    for raw_line in body.decode("utf-8-sig").splitlines():
        line = raw_line.strip()
        if line and not line.startswith("#"):
            return urljoin(parent_url, line)
    raise ValueError("playlist contains no media URL")


def assert_signed(url):
    query = parse_qs(urlparse(url).query)
    if not query.get("sig") or not query.get("exp") or query.get("kid") != ["v1"]:
        raise ValueError("playlist child URL is not signed with kid=v1")


failures = []
for master_key in master_keys:
    master_url = signer.build_url(
        cdn_base=settings.CDN_HLS_BASE_URL,
        path="/" + master_key,
        expires_at=int(time.time()) + 300,
        user_id=0,
    )
    try:
        master_status, master_body = fetch(master_url)
        if master_status != 200 or not master_body.startswith(b"#EXTM3U"):
            raise ValueError(f"master status={master_status}")
        variant_url = first_media_url(master_body, master_url)
        assert_signed(variant_url)
        variant_status, variant_body = fetch(variant_url)
        if variant_status != 200 or not variant_body.startswith(b"#EXTM3U"):
            raise ValueError(f"variant status={variant_status}")
        segment_url = first_media_url(variant_body, variant_url)
        assert_signed(segment_url)
        segment_status, segment_body = fetch(segment_url, range_request=True)
        if segment_status not in (200, 206) or not segment_body:
            raise ValueError(f"segment status={segment_status}")
        print(
            "CDN_PLAYBACK_CHAIN_PASS "
            f"master={master_status} variant={variant_status} segment={segment_status}"
        )
        break
    except (HTTPError, URLError, TimeoutError, ValueError) as exc:
        failures.append(f"{type(exc).__name__}:{exc}")
else:
    summary = "; ".join(failures[-3:])
    raise SystemExit(f"no HLS candidate completed the signed CDN chain: {summary}")
PY
); then
  echo "CANARY_FAIL video_playback_chain" >&2
  exit 45
fi
echo "$video_chain_proof"
echo "API_PREPROD_CANARY_PASS settings=prod database=preprod healthz=$healthz health=$health image=$image"
'@
    $remote = $remote.Replace("__EXPECTED_DATABASE__", $ExpectedDatabaseName).Replace("__EXPECTED_IMAGE__", $ImageUri)
    # PowerShell on Windows materializes here-strings with CRLF. Normalize the
    # bytes before sending the encoded script to Linux Bash.
    $remote = $remote.Replace("`r", "")
    # AWS-RunShellScript invokes each command through /bin/sh. The canary uses
    # bash-only pipefail and here-string behavior, so dispatch an encoded script
    # explicitly through bash instead of relying on the host's /bin/sh.
    $remoteB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($remote))
    $remoteCommand = "echo $remoteB64 | base64 -d | bash"
    $paramsRef = Convert-JsonArgToFileRef (
        @{
            commands = @($remoteCommand)
            executionTimeout = @([string]$TimeoutSec)
        } | ConvertTo-Json -Compress
    )
    $paramsFile = $paramsRef -replace '^file://', ''
    try {
        $sent = Invoke-AwsJson @(
            "ssm", "send-command",
            "--instance-ids", $instanceId,
            "--document-name", "AWS-RunShellScript",
            "--parameters", $paramsRef,
            "--timeout-seconds", [string]$TimeoutSec,
            "--region", $script:Region,
            "--output", "json"
        )
    } finally {
        Remove-TempFiles @($paramsFile)
    }
    $commandId = [string]$sent.Command.CommandId
    if (-not $commandId) { throw "API canary SSM command returned no command id." }

    $invocation = $null
    for ($elapsed = 0; $elapsed -lt $TimeoutSec; $elapsed += 5) {
        Start-Sleep -Seconds 5
        $invocation = Invoke-AwsJson @(
            "ssm", "get-command-invocation",
            "--command-id", $commandId,
            "--instance-id", $instanceId,
            "--region", $script:Region,
            "--output", "json"
        )
        if ([string]$invocation.Status -eq "Success") { break }
        if ([string]$invocation.Status -in @("Failed", "Cancelled", "TimedOut", "Cancelling")) { break }
    }
    if ([string]$invocation.Status -ne "Success") {
        $stderr = ([string]$invocation.StandardErrorContent).Trim()
        $stdout = ([string]$invocation.StandardOutputContent).Trim()
        throw "API pre-production canary failed: status=$($invocation.Status) stdout=$stdout stderr=$stderr"
    }
    $proof = ([string]$invocation.StandardOutputContent).Trim()
    if ($proof -notmatch "API_PREPROD_CANARY_PASS") {
        throw "API pre-production canary returned no PASS marker."
    }
    if ($proof -notmatch "CDN_PLAYBACK_CHAIN_PASS") {
        throw "API pre-production canary returned no CDN playback PASS marker."
    }
    Write-Host $proof -ForegroundColor Green
} finally {
    if ($instanceId) {
        Write-Host "Terminating canary instance $instanceId; expected state=terminated." -ForegroundColor DarkGray
        try {
            Invoke-Aws @(
                "ec2", "terminate-instances",
                "--instance-ids", $instanceId,
                "--region", $script:Region
            ) -ErrorMessage "terminate API canary instance" | Out-Null
            Invoke-Aws @(
                "ec2", "wait", "instance-terminated",
                "--instance-ids", $instanceId,
                "--region", $script:Region
            ) -ErrorMessage "wait for API canary termination" | Out-Null
            Write-Host "  Canary cleanup confirmed: $instanceId terminated" -ForegroundColor DarkGray
        } catch {
            $cleanupFailure = $_
        }
    }
    if ($cleanupFailure) { throw $cleanupFailure }
}
