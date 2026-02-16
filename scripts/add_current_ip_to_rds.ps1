# 현재 IP를 확인하고 RDS 보안 그룹에 추가

param(
    [string]$Region = "ap-northeast-2",
    [string]$SecurityGroupId = "sg-06cfb1f23372e2597"
)

$ErrorActionPreference = "Stop"

Write-Host "Adding current IP to RDS security group..." -ForegroundColor Cyan

# 현재 공인 IP 확인
Write-Host "`nGetting current public IP..." -ForegroundColor Gray
try {
    $publicIp = (Invoke-RestMethod -Uri "https://api.ipify.org" -TimeoutSec 5).Trim()
    Write-Host "  Current IP: $publicIp" -ForegroundColor Green
} catch {
    Write-Host "  Error: Could not get public IP" -ForegroundColor Red
    exit 1
}

$cidr = "$publicIp/32"

Write-Host "`nSecurity Group: $SecurityGroupId" -ForegroundColor Gray
Write-Host "Adding rule: PostgreSQL (5432) from $cidr" -ForegroundColor Gray

# 기존 규칙 확인
$existingRules = aws ec2 describe-security-groups --region $Region --group-ids $SecurityGroupId --query "SecurityGroups[0].IpPermissions[?FromPort==\`"5432\`" && ToPort==\`"5432\`"].IpRanges[].CidrIp" --output text 2>&1

$ruleExists = $false
if ($existingRules) {
    $rules = $existingRules.Trim() -split "`t"
    foreach ($rule in $rules) {
        if ($rule -eq $cidr) {
            $ruleExists = $true
            break
        }
    }
}

if ($ruleExists) {
    Write-Host "`n✓ Rule already exists: $cidr" -ForegroundColor Green
} else {
    Write-Host "`nAdding rule..." -ForegroundColor Gray
    $result = aws ec2 authorize-security-group-ingress `
        --region $Region `
        --group-id $SecurityGroupId `
        --protocol tcp `
        --port 5432 `
        --cidr $cidr `
        2>&1
    $exitCode = $LASTEXITCODE
    $errorMsg = $result -join "`n"

    if ($exitCode -eq 0) {
        Write-Host "✓ Rule added successfully: $cidr" -ForegroundColor Green
    } else {
        if ($errorMsg -match "already exists" -or $errorMsg -match "InvalidPermission.Duplicate" -or $errorMsg -match "already authorized") {
            Write-Host "✓ Rule already exists (detected by AWS)" -ForegroundColor Green
        } else {
            Write-Host "✗ Failed to add rule:" -ForegroundColor Red
            Write-Host $errorMsg -ForegroundColor Red
            Write-Host "`nTry adding manually in AWS Console:" -ForegroundColor Yellow
            Write-Host "  Security Group: $SecurityGroupId" -ForegroundColor White
            Write-Host "  Type: PostgreSQL, Port: 5432, Source: $cidr" -ForegroundColor White
            exit 1
        }
    }
}

Write-Host "`n✓ Done!" -ForegroundColor Green
Write-Host "`nNote: If RDS '인터넷 액세스 게이트웨이' is disabled, you still need SSH tunnel." -ForegroundColor Yellow
Write-Host "Check RDS '연결 및 보안' tab for '인터넷 액세스 게이트웨이' status." -ForegroundColor Yellow
