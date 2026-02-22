# API EC2 Role에 Batch SubmitJob 권한 부여 (Video upload_complete -> Batch 제출용)
# Usage: .\scripts\apply_api_batch_submit_policy.ps1
# Prerequisite: academy-ec2-role 존재

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RoleName = "academy-ec2-role"
$PolicyName = "BatchSubmitVideoJob"
$PolicyPath = "C:\academy\infra\worker_asg\iam_policy_api_batch_submit.json"

if (!(Test-Path $PolicyPath)) {
    throw "Policy file not found: $PolicyPath"
}

Write-Host "[1/3] Creating minified JSON (PowerShell-safe)..." -ForegroundColor Cyan
$minPath = "C:\academy\infra\worker_asg\iam_policy_api_batch_submit.min.json"
(Get-Content $PolicyPath -Raw | ConvertFrom-Json | ConvertTo-Json -Depth 10 -Compress) | Out-File $minPath -Encoding ascii

Write-Host "[2/3] Applying IAM policy to $RoleName..." -ForegroundColor Cyan
$fileUri = "file://" + ($minPath -replace '\\','/')
aws iam put-role-policy --role-name $RoleName --policy-name $PolicyName --policy-document $fileUri

Write-Host "[3/3] Verifying policy..." -ForegroundColor Cyan
$out = aws iam get-role-policy --role-name $RoleName --policy-name $PolicyName 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Verification failed: $out"
}
Write-Host $out
Write-Host ""
Write-Host "Done. academy-ec2-role can now call batch:SubmitJob. Test video upload_complete (no API restart required for IAM)." -ForegroundColor Green
