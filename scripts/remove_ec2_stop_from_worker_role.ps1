# ==============================================================================
# Block ec2:StopInstances on academy-ec2-role (prevent worker self-stop loop)
# Method: add Inline Deny policy (takes effect regardless of Allow)
# Usage: .\scripts\remove_ec2_stop_from_worker_role.ps1
# Requires: IAM put-role-policy permission
# ==============================================================================

$ErrorActionPreference = "Stop"
$InstanceProfileName = "academy-ec2-role"
$DenyPolicyName = "academy-deny-ec2-stop-instances"

Write-Host "`n=== STEP 1: Check role ===" -ForegroundColor Cyan
$roleName = aws iam get-instance-profile --instance-profile-name $InstanceProfileName --query "InstanceProfile.Roles[0].RoleName" --output text 2>$null
if (-not $roleName) {
    Write-Host "Instance profile '$InstanceProfileName' not found." -ForegroundColor Red
    exit 1
}
Write-Host "Role: $roleName" -ForegroundColor Green

Write-Host "`n=== STEP 2: Add ec2:StopInstances Deny policy ===" -ForegroundColor Cyan
$denyPolicy = '{"Version":"2012-10-17","Statement":[{"Sid":"DenyStopInstances","Effect":"Deny","Action":"ec2:StopInstances","Resource":["*"]}]}'

$policyPath = Join-Path $PSScriptRoot "academy_deny_stop_instances.json"
$utf8 = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($policyPath, $denyPolicy, $utf8)

Push-Location $PSScriptRoot
try {
    aws iam put-role-policy --role-name $roleName --policy-name $DenyPolicyName --policy-document "file://academy_deny_stop_instances.json"
} finally {
    Pop-Location
}
$ok = $LASTEXITCODE -eq 0
Remove-Item $policyPath -Force -ErrorAction SilentlyContinue

if (-not $ok) {
    Write-Host "FAILED. Check IAM put-role-policy permission." -ForegroundColor Red
    exit 1
}

Write-Host "OK. Deny policy '$DenyPolicyName' applied." -ForegroundColor Green
Write-Host "`n=== Result ===" -ForegroundColor Cyan
Write-Host "- Worker ec2.stop_instances() will be denied (AccessDenied)"
Write-Host "- ASG scale-in (TerminateInstances) unchanged (ASG service runs it)"
Write-Host "- Stop/start loop blocked"
Write-Host "`nDone." -ForegroundColor Green
