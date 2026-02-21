# API EC2 Role에 SQS SendMessage 권한 부여 (Video enqueue용)
# Usage: .\scripts\apply_api_sqs_send_policy.ps1
# Prerequisite: academy-ec2-role 존재

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RoleName = "academy-ec2-role"
$PolicyName = "SQSSendMessageVideoJobs"
$PolicyPath = "C:\academy\infra\worker_asg\iam_policy_api_sqs_send.json"

if (!(Test-Path $PolicyPath)) {
    throw "Policy file not found: $PolicyPath"
}

Write-Host "[1/3] Creating minified JSON (PowerShell-safe)..." -ForegroundColor Cyan
$raw = Get-Content $PolicyPath -Raw
$obj = $raw | ConvertFrom-Json
$minJson = $obj | ConvertTo-Json -Depth 10 -Compress
$minPath = "C:\academy\infra\worker_asg\iam_policy_api_sqs_send.min.json"
[System.IO.File]::WriteAllText($minPath, $minJson, [System.Text.UTF8Encoding]::new($false))

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
Write-Host "Done. Next: restart academy-api container on EC2, then test video upload." -ForegroundColor Green
