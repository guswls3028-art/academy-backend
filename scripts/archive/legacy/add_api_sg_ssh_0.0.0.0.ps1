# ==============================================================================
# academy-api-sg SSH 22 → 0.0.0.0/0 허용 추가
# "전부 안되거나 전부 되거나" 문제 해결 (IP 변동 시 SSH 차단 방지)
# Usage: .\scripts\add_api_sg_ssh_0.0.0.0.ps1 [-Region ap-northeast-2]
# ==============================================================================
param([string]$Region = "ap-northeast-2")
$ErrorActionPreference = "Stop"
$GroupId = "sg-0051cc8f79c04b058"

Write-Host "`n=== academy-api-sg SSH 22 → 0.0.0.0/0 허용 ===" -ForegroundColor Cyan
Write-Host "  GroupId: $GroupId" -ForegroundColor Gray

aws ec2 authorize-security-group-ingress `
  --group-id $GroupId `
  --protocol tcp `
  --port 22 `
  --cidr 0.0.0.0/0 `
  --region $Region
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: authorize-security-group-ingress failed" -ForegroundColor Red
    exit 1
}
Write-Host "  OK: 22/tcp from 0.0.0.0/0 added" -ForegroundColor Green
Write-Host "`n(Optional) Remove old /32 rule: aws ec2 revoke-security-group-ingress --group-id $GroupId --protocol tcp --port 22 --cidr 222.107.38.38/32 --region $Region`n" -ForegroundColor Gray
