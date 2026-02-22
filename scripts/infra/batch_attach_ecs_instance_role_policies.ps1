# Batch CE instance profile 역할에 ECS/ECR/CloudWatch 관리형 정책 부여
# academy-batch-ecs-instance-profile -> 해당 역할에만 부착. academy-ec2-role(API) 은 수정하지 않음.
# Usage: .\scripts\infra\batch_attach_ecs_instance_role_policies.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$InstanceProfileName = "academy-batch-ecs-instance-profile"

$Policies = @(
    "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/CloudWatchLogsFullAccess"
)

Write-Host "[1] Resolve role from instance profile: $InstanceProfileName" -ForegroundColor Cyan
$ip = aws iam get-instance-profile --instance-profile-name $InstanceProfileName --output json 2>&1 | ConvertFrom-Json
if (-not $ip -or -not $ip.InstanceProfile.Roles -or $ip.InstanceProfile.Roles.Count -eq 0) {
    Write-Host "  FAIL: Instance profile not found or has no role." -ForegroundColor Red
    exit 1
}
$RoleName = $ip.InstanceProfile.Roles[0].RoleName
Write-Host "  Role: $RoleName" -ForegroundColor Gray

Write-Host "[2] Attach managed policies to $RoleName only (no other roles modified)" -ForegroundColor Cyan
foreach ($policyArn in $Policies) {
    $shortName = $policyArn.Split("/")[-1]
    aws iam attach-role-policy --role-name $RoleName --policy-arn $policyArn 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  OK: $shortName" -ForegroundColor Green
    } else {
        Write-Host "  WARN: $shortName (may already be attached)" -ForegroundColor Yellow
    }
}

Write-Host "[3] Verify attached policies" -ForegroundColor Cyan
$attached = aws iam list-attached-role-policies --role-name $RoleName --output json | ConvertFrom-Json
$expected = @("AmazonEC2ContainerServiceforEC2Role", "AmazonEC2ContainerRegistryReadOnly", "CloudWatchLogsFullAccess")
foreach ($name in $expected) {
    $found = $attached.AttachedPolicies | Where-Object { $_.PolicyName -eq $name }
    if ($found) { Write-Host "  OK: $name" -ForegroundColor Green } else { Write-Host "  MISSING: $name" -ForegroundColor Red }
}

Write-Host ""
Write-Host "Done. ECS instances launched by Batch can now: 1) Join ECS cluster, 2) Pull from ECR, 3) Write CloudWatch Logs." -ForegroundColor Green
Write-Host "Verify: Submit a video job, then after ~120s run:" -ForegroundColor Gray
Write-Host "  aws batch list-jobs --job-queue academy-video-batch-queue --job-status RUNNING --region ap-northeast-2" -ForegroundColor Gray
Write-Host "  (RUNNABLE -> STARTING -> RUNNING within 120s = OK)" -ForegroundColor Gray
