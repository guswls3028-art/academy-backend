# ==============================================================================
# B1 Lambda Internal API 검증: X-Internal-Key passthrough (nginx) 확인
# EC2에서 PUBLIC(api.hakwonplus.com) vs LOCAL(localhost:8000) 호출 결과 비교
# ==============================================================================
# 사용: .\scripts\verify_lambda_internal_api.ps1
#      .\scripts\verify_lambda_internal_api.ps1 -InternalKey "your-key"
# ==============================================================================

param(
    [string]$KeyDir = "C:\key",
    [string]$InternalKey = "",
    [string]$Region = "ap-northeast-2"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
. (Join-Path $ScriptRoot "_config_instance_keys.ps1")

$apiKeyFile = $INSTANCE_KEY_FILES["academy-api"]
$apiKeyPath = Join-Path $KeyDir $apiKeyFile

# LAMBDA_INTERNAL_API_KEY: 파라미터 우선, 없으면 .env에서 추출
if (-not $InternalKey) {
    $envPath = Join-Path $RepoRoot ".env"
    if (Test-Path $envPath) {
        $line = Get-Content $envPath | Where-Object { $_ -match "^\s*LAMBDA_INTERNAL_API_KEY\s*=" } | Select-Object -First 1
        if ($line -match "=(.+)") {
            $InternalKey = $matches[1].Trim().Trim('"').Trim("'")
        }
    }
}
if (-not $InternalKey) {
    Write-Host "LAMBDA_INTERNAL_API_KEY not found. Set -InternalKey or add to .env" -ForegroundColor Red
    exit 1
}

$ips = @{}
$raw = aws ec2 describe-instances --region $Region `
    --filters "Name=instance-state-name,Values=running" "Name=tag:Name,Values=academy-api" `
    --query "Reservations[].Instances[].[Tags[?Key=='Name'].Value | [0], PublicIpAddress]" `
    --output text 2>&1
foreach ($line in ($raw -split "`n" | Where-Object { $_ -match "\S" })) {
    $p = $line.Trim() -split "\s+", 2
    if ($p.Length -ge 2 -and $p[1] -ne "None") { $ips[$p[0]] = $p[1] }
}

$apiIp = $ips["academy-api"]
if (-not $apiIp) {
    Write-Host "academy-api EC2 not found or no public IP." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $apiKeyPath)) {
    Write-Host "Key not found: $apiKeyPath" -ForegroundColor Red
    exit 1
}

$EC2_USER = "ec2-user"

# 원격 검증 스크립트 SCP 후 실행 (인라인 bash/python 따옴표 이슈 회피)
$remoteShPath = Join-Path $ScriptRoot "_verify_internal_api_remote.sh"
if (-not (Test-Path $remoteShPath)) {
    Write-Host "Remote script not found: $remoteShPath" -ForegroundColor Red
    exit 1
}

# base64로 전달 (특수문자·따옴표 이스케이프 이슈 회피)
$keyB64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($InternalKey))

Write-Host "Verifying Lambda internal API (academy-api @ $apiIp) ...`n" -ForegroundColor Cyan

scp -o StrictHostKeyChecking=accept-new -i $apiKeyPath $remoteShPath "${EC2_USER}@${apiIp}:/tmp/_verify_internal_api_remote.sh" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Failed to copy verify script to EC2" -ForegroundColor Red
    exit 1
}

$output = ssh -o StrictHostKeyChecking=accept-new -i $apiKeyPath "${EC2_USER}@${apiIp}" "export LIK_B64='$keyB64'; bash /tmp/_verify_internal_api_remote.sh" 2>&1

$inLocal = $false
$inPublic = $false
$localStatus = ""
$localBody = ""
$publicStatus = ""
$publicBody = ""

foreach ($line in ($output -split "`n")) {
    if ($line -eq "---LOCAL---") { $inLocal = $true; $inPublic = $false; continue }
    if ($line -eq "---PUBLIC---") { $inPublic = $true; $inLocal = $false; continue }
    if ($line -match "^STATUS:(.+)$") {
        if ($inLocal) { $localStatus = $matches[1].Trim() }
        if ($inPublic) { $publicStatus = $matches[1].Trim() }
        continue
    }
    if ($line -match "^BODY:(.*)$") {
        $b = $matches[1]
        if ($inLocal) { $localBody = $b }
        if ($inPublic) { $publicBody = $b }
    }
}

function Format-Status { param([string]$code)
    switch ($code) {
        "200" { "200 OK" }
        "403" { "403 Forbidden" }
        "000" { "ERR (connection failed)" }
        default { $code }
    }
}

# 출력
$localDisp = Format-Status $localStatus
$publicDisp = Format-Status $publicStatus

Write-Host "[LOCAL]  localhost:8000 -> $localDisp" -ForegroundColor $(if ($localStatus -eq "200") { "Green" } else { "Yellow" })
Write-Host "[PUBLIC] api.hakwonplus.com -> $publicDisp" -ForegroundColor $(if ($publicStatus -eq "200") { "Green" } else { "Yellow" })

if ($localStatus -eq "200" -or $publicStatus -eq "200") {
    $body = if ($localStatus -eq "200") { $localBody } else { $publicBody }
    if ($body) {
        Write-Host "`nBacklog count:" -ForegroundColor Gray
        Write-Host $body
    }
}

if ($localStatus -eq "403" -or $publicStatus -eq "403") {
    Write-Host "`n--- 403 Response body ---" -ForegroundColor Yellow
    if ($localStatus -eq "403" -and $localBody) { Write-Host "[LOCAL] $localBody" }
    if ($publicStatus -eq "403" -and $publicBody) { Write-Host "[PUBLIC] $publicBody" }
}
