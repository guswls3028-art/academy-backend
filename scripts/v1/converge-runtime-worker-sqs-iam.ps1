[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$AwsProfile = "default"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$PolicyPath = Join-Path $ScriptRoot "templates\iam\policy_workers_sqs.json"
$RoleName = "academy-ec2-role"
$PolicyName = "academy-workers-sqs"

if ($AwsProfile -and $AwsProfile.Trim()) {
    $env:AWS_PROFILE = $AwsProfile.Trim()
}
if (-not $env:AWS_DEFAULT_REGION) {
    $env:AWS_DEFAULT_REGION = "ap-northeast-2"
}

$script:PlanMode = -not $Apply
$script:ChangesMade = $false
$script:DeployLockAcquired = $false

. (Join-Path $ScriptRoot "core\env.ps1")
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\logging.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
. (Join-Path $ScriptRoot "core\guard.ps1")

Load-SSOT -Env "prod" | Out-Null
Assert-AwsMutationIdentity | Out-Null

function Get-WorkerSqsPolicyReadback {
    try {
        return Invoke-AwsJson @(
            "iam", "get-role-policy",
            "--role-name", $RoleName,
            "--policy-name", $PolicyName,
            "--output", "json"
        )
    } catch {
        return $null
    }
}

function Convert-PolicyToExactJson {
    param([Parameter(Mandatory = $true)]$PolicyDocument)
    return $PolicyDocument | ConvertTo-Json -Depth 50 -Compress
}

if (-not (Test-Path -LiteralPath $PolicyPath)) {
    throw "Runtime worker SQS policy template is missing."
}
$expectedDocument = Get-Content -Raw -LiteralPath $PolicyPath | ConvertFrom-Json
$expectedJson = Convert-PolicyToExactJson -PolicyDocument $expectedDocument
$before = Get-WorkerSqsPolicyReadback
$beforeJson = if ($before -and $before.PolicyDocument) {
    Convert-PolicyToExactJson -PolicyDocument $before.PolicyDocument
} else {
    ""
}
$matches = $beforeJson -eq $expectedJson

if (-not $Apply) {
    Write-Host (
        "RUNTIME_WORKER_SQS_IAM_PLAN role_exact=true policy_exact=true configured={0}" -f (
            $matches.ToString().ToLowerInvariant()
        )
    )
    exit 0
}

& (Join-Path $ScriptRoot "assert-production-source-freshness.ps1") -RepoRoot $RepoRoot
if ($LASTEXITCODE -ne 0) {
    throw "Production source freshness check failed."
}

try {
    Acquire-DeployLock -Reg $script:Region
    Assert-DeployLockAcquired -Reg $script:Region

    $current = Get-WorkerSqsPolicyReadback
    $currentJson = if ($current -and $current.PolicyDocument) {
        Convert-PolicyToExactJson -PolicyDocument $current.PolicyDocument
    } else {
        ""
    }
    if ($currentJson -ne $expectedJson) {
        Assert-DeployLockAcquired -Reg $script:Region
        Invoke-Aws @(
            "iam", "put-role-policy",
            "--role-name", $RoleName,
            "--policy-name", $PolicyName,
            "--policy-document", "file://$($PolicyPath -replace '\\','/')"
        ) -ErrorMessage "put exact runtime worker SQS policy" | Out-Null
    }

    Assert-DeployLockAcquired -Reg $script:Region
    $readback = Get-WorkerSqsPolicyReadback
    if (-not $readback -or -not $readback.PolicyDocument) {
        throw "Runtime worker SQS IAM readback is missing."
    }
    $readbackJson = Convert-PolicyToExactJson -PolicyDocument $readback.PolicyDocument
    if ($readbackJson -ne $expectedJson) {
        throw "Runtime worker SQS IAM readback mismatch."
    }
    Write-Host "RUNTIME_WORKER_SQS_IAM_RECONCILED role_exact=true policy_exact=true" -ForegroundColor Green
} finally {
    Release-DeployLock -Reg $script:Region
}
