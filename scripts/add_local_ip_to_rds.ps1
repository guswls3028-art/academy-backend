# ==============================================================================
# RDS 보안 그룹에 로컬 IP 추가 (직접 접속 허용)
# 사용: .\scripts\add_local_ip_to_rds.ps1
# ==============================================================================

param(
    [string]$Region = "ap-northeast-2",
    [string]$RdsIdentifier = "academy-db"
)

$ErrorActionPreference = "Stop"

# AWS 자격 증명 확인
if (-not $env:AWS_ACCESS_KEY_ID -or -not $env:AWS_SECRET_ACCESS_KEY) {
    Write-Host "AWS credentials not set. Please set:" -ForegroundColor Yellow
    Write-Host "  `$env:AWS_ACCESS_KEY_ID = 'YOUR_KEY'" -ForegroundColor White
    Write-Host "  `$env:AWS_SECRET_ACCESS_KEY = 'YOUR_SECRET'" -ForegroundColor White
    Write-Host "  `$env:AWS_DEFAULT_REGION = 'ap-northeast-2'" -ForegroundColor White
    exit 1
}

Write-Host "Adding local IP to RDS security group..." -ForegroundColor Cyan

# 1) RDS 인스턴스 정보 확인
Write-Host "`n[1/3] Finding RDS instance..." -ForegroundColor Gray
$rdsInfo = aws rds describe-db-instances --region $Region --db-instance-identifier $RdsIdentifier --query "DBInstances[0].[VpcSecurityGroups[0].VpcSecurityGroupId,Endpoint.Address]" --output text 2>&1
$rdsExitCode = $LASTEXITCODE

if ($rdsExitCode -ne 0 -or -not $rdsInfo -or $rdsInfo -match "error|Error|not found") {
    Write-Host "Error: Failed to get RDS instance info." -ForegroundColor Red
    Write-Host "AWS CLI output: $rdsInfo" -ForegroundColor Yellow
    Write-Host "Exit code: $rdsExitCode" -ForegroundColor Yellow
    exit 1
}

$parts = $rdsInfo.Trim() -split "`t", 2
$securityGroupId = $parts[0]
$rdsEndpoint = $parts[1]

Write-Host "  RDS Endpoint: $rdsEndpoint" -ForegroundColor Gray
Write-Host "  Security Group: $securityGroupId" -ForegroundColor Gray

# 2) 현재 공인 IP 확인
Write-Host "`n[2/3] Getting current public IP..." -ForegroundColor Gray
try {
    $publicIp = (Invoke-RestMethod -Uri "https://api.ipify.org" -TimeoutSec 5).Trim()
    Write-Host "  Public IP: $publicIp" -ForegroundColor Gray
} catch {
    Write-Host "  Warning: Could not get public IP. Using 0.0.0.0/0 (all IPs)" -ForegroundColor Yellow
    $publicIp = "0.0.0.0/0"
}

# CIDR 형식으로 변환 (단일 IP는 /32 추가)
$cidr = if ($publicIp -match "^(\d+\.\d+\.\d+\.\d+)$") {
    "$publicIp/32"
} else {
    $publicIp
}

# 3) 보안 그룹에 인바운드 규칙 추가
Write-Host "`n[3/3] Adding inbound rule to security group..." -ForegroundColor Gray
Write-Host "  Rule: PostgreSQL (5432) from $cidr" -ForegroundColor Gray

# 기존 규칙 확인 (더 정확한 방법)
$existingRules = aws ec2 describe-security-groups --region $Region --group-ids $securityGroupId --query "SecurityGroups[0].IpPermissions[?FromPort==\`"5432\`" && ToPort==\`"5432\`"].IpRanges[].CidrIp" --output text 2>&1

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
    Write-Host "  ✓ Rule already exists. Skipping." -ForegroundColor Green
} else {
    $result = aws ec2 authorize-security-group-ingress `
        --region $Region `
        --group-id $securityGroupId `
        --protocol tcp `
        --port 5432 `
        --cidr $cidr `
        2>&1
    $addExitCode = $LASTEXITCODE
    $errorMsg = $result -join "`n"

    if ($addExitCode -eq 0) {
        Write-Host "  ✓ Rule added successfully" -ForegroundColor Green
    } else {
        if ($errorMsg -match "already exists" -or $errorMsg -match "InvalidPermission.Duplicate" -or $errorMsg -match "already authorized") {
            Write-Host "  ✓ Rule already exists (detected by AWS)" -ForegroundColor Green
        } else {
            Write-Host "  ✗ Failed to add rule:" -ForegroundColor Red
            Write-Host "  Exit code: $addExitCode" -ForegroundColor Yellow
            Write-Host "  Error: $errorMsg" -ForegroundColor Red
            Write-Host "`nTroubleshooting:" -ForegroundColor Yellow
            Write-Host "  1. Check AWS credentials are correct" -ForegroundColor White
            Write-Host "  2. Check you have ec2:AuthorizeSecurityGroupIngress permission" -ForegroundColor White
            Write-Host "  3. Try manually: aws ec2 authorize-security-group-ingress --region $Region --group-id $securityGroupId --protocol tcp --port 5432 --cidr $cidr" -ForegroundColor White
            exit 1
        }
    }
}

Write-Host "`n✓ Done! You can now connect to RDS directly from your local machine." -ForegroundColor Green
Write-Host "`nUpdate .env.local:" -ForegroundColor Yellow
Write-Host "  DB_HOST=$rdsEndpoint" -ForegroundColor White
Write-Host "  DB_PORT=5432" -ForegroundColor White
