# ==============================================================================
# API 502 원인 점검: ALB 타깃 그룹 상태, API 보안 그룹 인바운드
# 사용: 루트 액세스 키 env 설정 후 .\scripts\check_api_alb.ps1
# ==============================================================================
param([string]$Region = "ap-northeast-2")

$ApiSgId = "sg-0051cc8f79c04b058"  # academy-api-sg

Write-Host "`n=== API 502 점검 (ALB / 타깃 / 보안그룹) ===`n" -ForegroundColor Cyan

# 1) 로드밸런서 목록 (api 관련)
Write-Host "[1] 로드밸런서 (api.hakwonplus.com 용)" -ForegroundColor White
$albs = aws elbv2 describe-load-balancers --region $Region --query "LoadBalancers[*].[LoadBalancerArn,DNSName,Scheme]" --output text 2>$null
if (-not $albs) { Write-Host "  (없음 또는 권한 없음)" -ForegroundColor Yellow } else {
    $albs -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ } | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
}

# 2) 타깃 그룹 → API 인스턴스 헬스
Write-Host "`n[2] 타깃 그룹별 타깃 상태" -ForegroundColor White
$tgs = aws elbv2 describe-target-groups --region $Region --query "TargetGroups[*].TargetGroupArn" --output text 2>$null
if ($tgs) {
    foreach ($tg in ($tgs -split "\s+" | Where-Object { $_ })) {
        $name = aws elbv2 describe-target-groups --target-group-arns $tg --region $Region --query "TargetGroups[0].TargetGroupName" --output text 2>$null
        $health = aws elbv2 describe-target-health --target-group-arn $tg --region $Region --query "TargetHealthDescriptions[*].[Target.Id,TargetHealth.State,TargetHealth.Reason]" --output text 2>$null
        if ($name -match "api") {
            Write-Host "  $name" -ForegroundColor Gray
            if ($health) { $health -split "`n" | ForEach-Object { Write-Host "    $_" } } else { Write-Host "    (타깃 없음 또는 unhealthy)" -ForegroundColor Red }
        }
    }
} else { Write-Host "  (타깃 그룹 없음)" -ForegroundColor Yellow }

# 3) API 인스턴스가 8000 포트 열었는지 (API SG 인바운드)
Write-Host "`n[3] API 보안그룹 ($ApiSgId) 인바운드 8000" -ForegroundColor White
$sgJson = aws ec2 describe-security-groups --group-ids $ApiSgId --region $Region --output json 2>$null
if ($sgJson -match "8000") {
    Write-Host "  8000 포트 규칙 있음" -ForegroundColor Green
} else {
    Write-Host "  8000 포트 인바운드 없음 → ALB에서 API 접속 불가 → 502 가능" -ForegroundColor Red
    Write-Host "  수정: ALB 보안그룹 ID 확인 후 아래 실행" -ForegroundColor Yellow
    Write-Host "  aws ec2 authorize-security-group-ingress --group-id $ApiSgId --protocol tcp --port 8000 --source-group ALB_SG_ID --region $Region" -ForegroundColor Gray
    Write-Host "  (ALB_SG_ID 자리에 ALB 보안그룹 ID 넣기, 예: sg-0abc1234)" -ForegroundColor Gray
}

# 4) academy-api 인스턴스 확인
Write-Host "`n[4] academy-api 인스턴스" -ForegroundColor White
$apiIp = aws ec2 describe-instances --region $Region --filters "Name=tag:Name,Values=academy-api" "Name=instance-state-name,Values=running" --query "Reservations[0].Instances[0].PublicIpAddress" --output text 2>$null
if ($apiIp -and $apiIp -ne "None") {
    Write-Host "  PublicIP: $apiIp" -ForegroundColor Green
    Write-Host "  (서버에서 curl -s http://localhost:8000/health → 200 이어야 함)" -ForegroundColor Gray
} else {
    Write-Host "  실행 중인 academy-api 없음" -ForegroundColor Red
}

Write-Host "`n=== Done ===`n" -ForegroundColor Cyan
