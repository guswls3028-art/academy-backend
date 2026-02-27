# API 502 check: ALB, target group, API SG port 8000
# Usage: set root AWS env then .\scripts\check_api_alb.ps1
param([string]$Region = "ap-northeast-2")

$ApiSgId = "sg-0051cc8f79c04b058"

Write-Host ""
Write-Host "=== API 502 check (ALB / Target / SG) ===" -ForegroundColor Cyan
Write-Host ""

Write-Host "[1] Load balancers" -ForegroundColor White
$albs = aws elbv2 describe-load-balancers --region $Region --query "LoadBalancers[*].[LoadBalancerArn,DNSName,Scheme]" --output text 2>$null
if (-not $albs) { Write-Host "  (none or no permission)" -ForegroundColor Yellow } else {
    $albs -split "`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ } | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
}

Write-Host ""
Write-Host "[2] Target group health" -ForegroundColor White
$tgs = aws elbv2 describe-target-groups --region $Region --query "TargetGroups[*].TargetGroupArn" --output text 2>$null
if ($tgs) {
    foreach ($tg in ($tgs -split "\s+" | Where-Object { $_ })) {
        $name = aws elbv2 describe-target-groups --target-group-arns $tg --region $Region --query "TargetGroups[0].TargetGroupName" --output text 2>$null
        $health = aws elbv2 describe-target-health --target-group-arn $tg --region $Region --query "TargetHealthDescriptions[*].[Target.Id,TargetHealth.State,TargetHealth.Reason]" --output text 2>$null
        if ($name -match "api") {
            Write-Host "  $name" -ForegroundColor Gray
            if ($health) { $health -split "`n" | ForEach-Object { Write-Host "    $_" } } else { Write-Host "    (no target or unhealthy)" -ForegroundColor Red }
        }
    }
} else { Write-Host "  (no target groups)" -ForegroundColor Yellow }

Write-Host ""
Write-Host "[3] API SG $ApiSgId port 8000" -ForegroundColor White
$sgJson = aws ec2 describe-security-groups --group-ids $ApiSgId --region $Region --output json 2>$null
if ($sgJson -match "8000") {
    Write-Host "  port 8000 rule exists" -ForegroundColor Green
} else {
    Write-Host "  no port 8000 inbound - ALB cannot reach API - 502 likely" -ForegroundColor Red
    $albSgId = aws elbv2 describe-load-balancers --region $Region --query "LoadBalancers[0].SecurityGroups[0]" --output text 2>$null
    if ($albSgId -and $albSgId -ne "None") {
        Write-Host "  Run this (ALB SG: $albSgId):" -ForegroundColor Yellow
        Write-Host "  aws ec2 authorize-security-group-ingress --group-id $ApiSgId --protocol tcp --port 8000 --source-group $albSgId --region $Region" -ForegroundColor Gray
    } else {
        Write-Host "  Get ALB SG ID from console then run authorize-security-group-ingress" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "[4] academy-api instance" -ForegroundColor White
$apiIp = aws ec2 describe-instances --region $Region --filters "Name=tag:Name,Values=academy-api" "Name=instance-state-name,Values=running" --query "Reservations[0].Instances[0].PublicIpAddress" --output text 2>$null
if ($apiIp -and $apiIp -ne "None") {
    Write-Host "  PublicIP: $apiIp" -ForegroundColor Green
    Write-Host "  (on server: curl -s http://localhost:8000/health should return 200)" -ForegroundColor Gray
} else {
    Write-Host "  no running academy-api instance" -ForegroundColor Red
}

Write-Host ""
Write-Host "=== Done ===" -ForegroundColor Cyan
Write-Host ""
