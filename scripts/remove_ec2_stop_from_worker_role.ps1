# ==============================================================================
# academy-ec2-role에서 ec2:StopInstances 차단 (Worker self-stop 루프 방지)
# 방법: Inline Deny 정책 추가 (Allow 출처와 무관하게 즉시 차단)
# 사용: .\scripts\remove_ec2_stop_from_worker_role.ps1
# 전제: IAM put-role-policy 권한 필요
# ==============================================================================

$ErrorActionPreference = "Stop"
$InstanceProfileName = "academy-ec2-role"
$DenyPolicyName = "academy-deny-ec2-stop-instances"

Write-Host "`n=== STEP 1: Role 확인 ===" -ForegroundColor Cyan
$roleName = aws iam get-instance-profile --instance-profile-name $InstanceProfileName --query "InstanceProfile.Roles[0].RoleName" --output text 2>$null
if (-not $roleName) {
    Write-Host "Instance profile '$InstanceProfileName' not found." -ForegroundColor Red
    exit 1
}
Write-Host "Role: $roleName" -ForegroundColor Green

Write-Host "`n=== STEP 2: ec2:StopInstances Deny 정책 추가 ===" -ForegroundColor Cyan
$denyPolicy = '{"Version":"2012-10-17","Statement":[{"Sid":"DenyStopInstances","Effect":"Deny","Action":"ec2:StopInstances","Resource":"*"}]}'

aws iam put-role-policy --role-name $roleName --policy-name $DenyPolicyName --policy-document $denyPolicy
if ($LASTEXITCODE -ne 0) {
    Write-Host "FAILED. IAM put-role-policy 권한 확인." -ForegroundColor Red
    exit 1
}

Write-Host "OK. Deny 정책 '$DenyPolicyName' 적용 완료." -ForegroundColor Green
Write-Host "`n=== 결과 ===" -ForegroundColor Cyan
Write-Host "- Worker가 ec2.stop_instances() 호출 시 거부됨 (AccessDenied)"
Write-Host "- ASG 스케일 인(TerminateInstances)은 영향 없음 (ASG 서비스가 실행)"
Write-Host "- 껐다 켜짐 루프 즉시 차단"
Write-Host "`nDone." -ForegroundColor Green
