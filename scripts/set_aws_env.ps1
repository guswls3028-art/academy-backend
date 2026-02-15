# AWS CLI/ECR 인증 — .env.aws 에서 읽어서 현재 세션에 설정
# 사용: . .\scripts\set_aws_env.ps1   또는  & .\scripts\set_aws_env.ps1 (자식 프로세스면 현재 셸엔 반영 안 됨 → 호출: . .\scripts\set_aws_env.ps1)
$envFile = Join-Path (Get-Location) ".env.aws"
if (-not (Test-Path $envFile)) {
    Write-Host "없음: .env.aws ( .env.aws.example 복사 후 값 채우기 )" -ForegroundColor Yellow
    exit 1
}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+)\s*$') {
        [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim(), 'Process')
    }
}
Write-Host "OK: AWS env loaded from .env.aws" -ForegroundColor Green
