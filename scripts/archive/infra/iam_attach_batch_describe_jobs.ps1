# ==============================================================================
# Attach Managed Policy (AcademyAllowBatchDescribeJobs) to the role used by reconcile job.
# Role: discovered from Batch job definition academy-video-ops-reconcile (jobRoleArn), else academy-video-batch-job-role.
# Reconcile job runs with this role and needs batch:DescribeJobs / batch:ListJobs.
# Inline policy not used; Managed Policy only. Idempotent.
# Usage: .\scripts\infra\iam_attach_batch_describe_jobs.ps1 [-Region ap-northeast-2]
# ==============================================================================

param([string]$Region = "ap-northeast-2")
try { $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch {}
$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent (Split-Path -Parent $ScriptRoot)

$PolicyName = "AcademyAllowBatchDescribeJobs"
$FallbackRoleName = "academy-video-batch-job-role"
$ReconcileJobDefName = "academy-video-ops-reconcile"

function ExecJson($argsArray) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @argsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) { return $null }
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

$AccountId = (aws sts get-caller-identity --query Account --output text 2>&1)
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: AWS identity check failed" -ForegroundColor Red; exit 1 }

# Discover role from reconcile job definition (jobRoleArn)
$RoleName = $FallbackRoleName
$jdResp = ExecJson @("batch", "describe-job-definitions", "--job-definition-name", $ReconcileJobDefName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
if ($jdResp -and $jdResp.jobDefinitions -and $jdResp.jobDefinitions.Count -gt 0) {
    $jobRoleArn = $jdResp.jobDefinitions[0].containerProperties.jobRoleArn -as [string]
    if (-not [string]::IsNullOrWhiteSpace($jobRoleArn)) {
        if ($jobRoleArn -match '([^/]+)$') { $RoleName = $Matches[1] }
    }
}
Write-Host "Target role: $RoleName (from job def or fallback)" -ForegroundColor Gray

$PolicyArn = "arn:aws:iam::${AccountId}:policy/$PolicyName"
$policyDoc = '{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":["batch:DescribeJobs","batch:ListJobs"],"Resource":"*"}]}'

# Create policy if not exists
$policyOut = ExecJson @("iam", "get-policy", "--policy-arn", $PolicyArn, "--output", "json")
if (-not $policyOut -or -not $policyOut.Policy) {
    Write-Host "Creating Managed Policy: $PolicyName" -ForegroundColor Cyan
    $tempFile = Join-Path ([System.IO.Path]::GetTempPath()) "AcademyAllowBatchDescribeJobs.json"
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($tempFile, $policyDoc, $utf8NoBom)
    try {
        $createOut = & aws iam create-policy --policy-name $PolicyName --policy-document "file://$($tempFile -replace '\\','/')" --description "Allows Batch job role DescribeJobs/ListJobs for reconcile" --output json 2>&1
        if ($LASTEXITCODE -ne 0) {
            if (($createOut | Out-String) -match "EntityAlreadyExists") {
                Write-Host "Policy already exists (another process created it)." -ForegroundColor Gray
            } else {
                Write-Host "FAIL: create-policy: $createOut" -ForegroundColor Red
                exit 1
            }
        } else {
            Write-Host "Created policy: $PolicyName" -ForegroundColor Green
        }
    } finally {
        if (Test-Path -LiteralPath $tempFile) { Remove-Item $tempFile -Force -ErrorAction SilentlyContinue }
    }
} else {
    Write-Host "Managed Policy exists: $PolicyName" -ForegroundColor Gray
}

# Attach to role
$attached = ExecJson @("iam", "list-attached-role-policies", "--role-name", $RoleName, "--output", "json")
$already = $false
if ($attached -and $attached.AttachedPolicies) {
    foreach ($a in $attached.AttachedPolicies) {
        if ($a.PolicyArn -eq $PolicyArn) { $already = $true; break }
    }
}
if ($already) {
    Write-Host "Policy already attached to role: $RoleName" -ForegroundColor Green
    exit 0
}

aws iam attach-role-policy --role-name $RoleName --policy-arn $PolicyArn
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: attach-role-policy failed" -ForegroundColor Red; exit 1 }
Write-Host "Attached $PolicyName to $RoleName" -ForegroundColor Green
