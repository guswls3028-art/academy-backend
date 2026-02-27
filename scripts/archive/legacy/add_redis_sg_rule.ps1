# ==============================================================================
# API 서버 보안 그룹에 Redis 6379 인바운드 추가
# 워커가 API 서버의 Redis에 접속하려면 필요
# Usage: .\scripts\add_redis_sg_rule.ps1 [-SecurityGroupId sg-xxx] [-Region ap-northeast-2]
# ==============================================================================

param(
    [string]$SecurityGroupId = "sg-02692600fbf8e26f7",
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
Write-Host "`n=== Redis 6379 보안 그룹 인바운드 추가 ===" -ForegroundColor Cyan
Write-Host "  SecurityGroupId: $SecurityGroupId" -ForegroundColor Gray
Write-Host "  Region: $Region`n" -ForegroundColor Gray

# 자기 자신(동일 SG)에서 6379 허용 (API+워커 같은 SG 사용)
aws ec2 authorize-security-group-ingress `
    --group-id $SecurityGroupId `
    --protocol tcp `
    --port 6379 `
    --source-group $SecurityGroupId `
    --region $Region 2>&1

if ($LASTEXITCODE -eq 0) {
    Write-Host "OK: Redis 6379 인바운드 추가됨 (동일 SG 내 통신 허용)" -ForegroundColor Green
} else {
    if ($LASTEXITCODE -ne 0) {
        $err = $Error[0]
        if ($err -match "already exists" -or $LASTEXITCODE -eq 254) {
            Write-Host "이미 6379 규칙이 있음. 스킵." -ForegroundColor Yellow
            exit 0
        }
    }
    Write-Host "FAIL: aws ec2 authorize-security-group-ingress 실패" -ForegroundColor Red
    exit 1
}
