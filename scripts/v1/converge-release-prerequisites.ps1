# Converge only the immutable-release control plane. This intentionally does
# not resolve a release manifest, create a Launch Template, refresh an ASG, or
# register a Batch job definition. It is the one-time bridge before the first
# complete/successful release manifest exists.
[CmdletBinding()]
param(
    [ValidateSet("prod", "staging", "dev")]
    [string]$Env = "prod",
    [string]$AwsProfile = "default"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
if ($AwsProfile -and $AwsProfile.Trim()) {
    $env:AWS_PROFILE = $AwsProfile.Trim()
}
if (-not $env:AWS_DEFAULT_REGION) {
    $env:AWS_DEFAULT_REGION = "ap-northeast-2"
}

$script:PlanMode = $false
$script:ChangesMade = $false
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\logging.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
. (Join-Path $ScriptRoot "core\guard.ps1")
. (Join-Path $ScriptRoot "resources\ecr.ps1")
. (Join-Path $ScriptRoot "resources\iam.ps1")
. (Join-Path $ScriptRoot "resources\dynamodb.ps1")

Load-SSOT -Env $Env | Out-Null

# The shared mutation lock must exist before the first regular deploy can
# acquire it. This idempotent prerequisite is the only pre-lock bootstrap.
Ensure-DynamoLockTable
$script:DeployLockAcquired = $false
Acquire-DeployLock -Reg $script:Region
try {

# IAM can be least-privilege only after all four existing runtime Launch
# Templates have stable IDs. A missing LT is a blocker, never a reason to
# widen the policy or create runtime infrastructure from this bridge script.
$requiredLaunchTemplates = @(
    $script:ApiLaunchTemplateName,
    $script:MessagingLaunchTemplateName,
    $script:AiLaunchTemplateName,
    $script:ToolsLaunchTemplateName
) | Where-Object { $_ -and $_.Trim() } | Sort-Object -Unique
if ($requiredLaunchTemplates.Count -ne 4) {
    throw "Immutable-release prerequisite convergence requires exactly four SSOT Launch Templates."
}
$ltResult = Invoke-AwsJson (
    @("ec2", "describe-launch-templates", "--launch-template-names") +
    [string[]]$requiredLaunchTemplates +
    @("--region", $script:Region, "--output", "json")
)
$foundLaunchTemplates = @($ltResult.LaunchTemplates | Where-Object { $_.LaunchTemplateId } | Select-Object -ExpandProperty LaunchTemplateName -Unique)
if ($foundLaunchTemplates.Count -ne 4) {
    throw "All four existing runtime Launch Templates must exist before release prerequisite convergence; found=$($foundLaunchTemplates.Count)."
}

Ensure-ECRRepos
Ensure-GitHubActionsDeployIAM

Write-Host "RELEASE_PREREQUISITES_CONVERGED lockTable=active ecr=6 launchTemplates=4 iam=exact" -ForegroundColor Green
} finally {
    Release-DeployLock -Reg $script:Region
}
