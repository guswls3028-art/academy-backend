# SSOT v3 — Preflight: AWS Identity, SSM 존재, 필수 값
$ErrorActionPreference = "Stop"

function Invoke-PreflightCheck {
    param(
        [string]$Region = $script:Region
    )
    Write-Step "Preflight"

    # AWS CLI
    $v = aws --version 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Fail "aws CLI not found"; throw "Preflight failed" }
    Write-Host "  aws: $v" -ForegroundColor Gray

    # Identity
    $account = aws sts get-caller-identity --query Account --output text --region $Region 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Fail "AWS identity failed"; throw "Preflight failed" }
    Write-Ok "Account $account"

    # SSM workers env 존재
    $param = aws ssm get-parameter --name $script:SsmWorkersEnv --region $Region --query Parameter.Name --output text 2>&1
    if ($LASTEXITCODE -ne 0) { Write-Fail "SSM $script:SsmWorkersEnv not found"; throw "Preflight failed" }
    Write-Ok "SSM $script:SsmWorkersEnv exists"

    Write-Ok "Preflight passed"
}
