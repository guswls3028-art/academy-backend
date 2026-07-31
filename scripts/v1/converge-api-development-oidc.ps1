# Converge the separate customer-managed GitHub OIDC policy used only by the
# persistent development runtime. The production inline deploy policy and
# exact main-ref plus approved-production-environment OIDC trust are read back
# but never broadened here.
[CmdletBinding()]
param(
    [string]$AwsProfile = "default"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
if ($AwsProfile -and $AwsProfile.Trim()) {
    $env:AWS_PROFILE = $AwsProfile.Trim()
}
if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }

$script:PlanMode = $false
. (Join-Path $ScriptRoot "core\env.ps1")
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
Assert-AwsMutationIdentity | Out-Null
Load-SSOT -Env prod | Out-Null

$policyName = [string]$script:GitHubActionsDevelopmentDeployPolicyName
$policyArn = "arn:aws:iam::$($script:AccountId):policy/$policyName"
$roleName = [string]$script:GitHubActionsDeployRoleName
$policyPath = Join-Path (
    Resolve-Path (Join-Path $ScriptRoot "..\..")
) "infra\worker_asg\iam_policy_gha_development_deploy.json"
if (-not (Test-Path -LiteralPath $policyPath)) {
    throw "Development GitHub OIDC policy file not found: $policyPath"
}

try {
    $expected = Get-Content -LiteralPath $policyPath -Raw | ConvertFrom-Json
} catch {
    throw "Development GitHub OIDC policy is not valid JSON."
}
$expectedJson = $expected | ConvertTo-Json -Depth 30 -Compress

$trust = Invoke-AwsJson @(
    "iam", "get-role",
    "--role-name", $roleName,
    "--output", "json"
)
$trustStatement = @($trust.Role.AssumeRolePolicyDocument.Statement)
if ($trustStatement.Count -ne 1) {
    throw "GitHub OIDC trust must contain exactly one statement."
}
$condition = $trustStatement[0].Condition.StringEquals
$expectedProvider = (
    "arn:aws:iam::$($script:AccountId):oidc-provider/" +
    "token.actions.githubusercontent.com"
)
$expectedSubjects = @(
    "repo:guswls3028-art/academy-backend:environment:production",
    "repo:guswls3028-art/academy-backend:ref:refs/heads/main"
) | Sort-Object
$actualSubjects = @(
    $condition.'token.actions.githubusercontent.com:sub'
) | Sort-Object
if (
    [string]$trustStatement[0].Principal.Federated -ne $expectedProvider -or
    [string]$condition.'token.actions.githubusercontent.com:aud' -ne "sts.amazonaws.com" -or
    ($actualSubjects -join "`n") -cne ($expectedSubjects -join "`n")
) {
    throw "GitHub OIDC trust must remain backend main-ref and production-environment only."
}

$current = Invoke-AwsJson @(
    "iam", "get-policy",
    "--policy-arn", $policyArn,
    "--output", "json"
)
if (-not $current) {
    $policyRef = Convert-JsonArgToFileRef $expectedJson
    $policyFile = $policyRef -replace '^file://', ''
    try {
        Invoke-Aws @(
            "iam", "create-policy",
            "--policy-name", $policyName,
            "--description",
            "Exact GitHub OIDC permissions for the isolated Academy development runtime",
            "--policy-document", $policyRef,
            "--tags",
            "Key=Project,Value=academy",
            "Key=Environment,Value=development",
            "Key=ManagedBy,Value=academy-bootstrap"
        ) -ErrorMessage "create development GitHub OIDC policy" | Out-Null
    } finally {
        Remove-TempFiles @($policyFile)
    }
} else {
    $defaultVersion = [string]$current.Policy.DefaultVersionId
    $version = Invoke-AwsJson @(
        "iam", "get-policy-version",
        "--policy-arn", $policyArn,
        "--version-id", $defaultVersion,
        "--output", "json"
    )
    $currentJson = $version.PolicyVersion.Document | ConvertTo-Json -Depth 30 -Compress
    if ($currentJson -ne $expectedJson) {
        $versions = Invoke-AwsJson @(
            "iam", "list-policy-versions",
            "--policy-arn", $policyArn,
            "--output", "json"
        )
        foreach ($old in @($versions.Versions | Where-Object { -not $_.IsDefaultVersion })) {
            Invoke-Aws @(
                "iam", "delete-policy-version",
                "--policy-arn", $policyArn,
                "--version-id", [string]$old.VersionId
            ) -ErrorMessage "delete stale development OIDC policy version" | Out-Null
        }
        $policyRef = Convert-JsonArgToFileRef $expectedJson
        $policyFile = $policyRef -replace '^file://', ''
        try {
            Invoke-Aws @(
                "iam", "create-policy-version",
                "--policy-arn", $policyArn,
                "--policy-document", $policyRef,
                "--set-as-default"
            ) -ErrorMessage "update development GitHub OIDC policy" | Out-Null
        } finally {
            Remove-TempFiles @($policyFile)
        }
    }
}

Invoke-Aws @(
    "iam", "attach-role-policy",
    "--role-name", $roleName,
    "--policy-arn", $policyArn
) -ErrorMessage "attach development GitHub OIDC policy" | Out-Null

$readback = Invoke-AwsJson @(
    "iam", "get-policy",
    "--policy-arn", $policyArn,
    "--output", "json"
)
$readbackVersion = Invoke-AwsJson @(
    "iam", "get-policy-version",
    "--policy-arn", $policyArn,
    "--version-id", [string]$readback.Policy.DefaultVersionId,
    "--output", "json"
)
$actualJson = $readbackVersion.PolicyVersion.Document |
    ConvertTo-Json -Depth 30 -Compress
if ($actualJson -ne $expectedJson) {
    throw "Development GitHub OIDC managed policy readback mismatch."
}
$attached = Invoke-AwsJson @(
    "iam", "list-attached-role-policies",
    "--role-name", $roleName,
    "--output", "json"
)
if (
    @($attached.AttachedPolicies).Count -ne 1 -or
    [string]$attached.AttachedPolicies[0].PolicyArn -ne $policyArn
) {
    throw "Development GitHub OIDC policy must be the role's only attached managed policy."
}

Write-Host (
    "API_DEVELOPMENT_OIDC_PASS role={0} policy={1} trust=main-and-production-environment" -f
    $roleName,
    $policyArn
) -ForegroundColor Green
