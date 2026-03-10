# Cloudflare API로 zone별 네임서버 조회 (가비아에 붙여넣을 값 확인용)
# 사용: .\scripts\get-cloudflare-nameservers.ps1
# .env의 CLOUDFLARE_EMAIL, CLOUDFLARE_API_KEY 사용 (출력하지 않음)

$ErrorActionPreference = 'Stop'
$envFile = Join-Path $PSScriptRoot '..\..\backend\.env'
if (-not (Test-Path $envFile)) {
    Write-Host "backend\.env not found."
    exit 1
}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^CLOUDFLARE_(EMAIL|API_KEY|ACCOUNT_ID)=(.+)$') {
        [System.Environment]::SetEnvironmentVariable("CLOUDFLARE_$($matches[1])", $matches[2].Trim(), 'Process')
    }
}
$email = $env:CLOUDFLARE_EMAIL
$key   = $env:CLOUDFLARE_API_KEY
if (-not $email -or -not $key) {
    Write-Host "CLOUDFLARE_EMAIL and CLOUDFLARE_API_KEY must be set in backend\.env"
    exit 1
}

$headers = @{
    'X-Auth-Email' = $email
    'X-Auth-Key'   = $key
    'Content-Type'  = 'application/json'
}
$uri = 'https://api.cloudflare.com/client/v4/zones?per_page=50'
try {
    $r = Invoke-RestMethod -Uri $uri -Headers $headers -Method Get
} catch {
    Write-Host "API error: $_"
    exit 1
}
if (-not $r.success) {
    Write-Host "API returned success=false"
    exit 1
}

Write-Host ""
Write-Host "=== Cloudflare Zone 네임서버 (가비아 '네임서버 설정'에 넣을 값) ==="
Write-Host ""
foreach ($z in $r.result) {
    Write-Host "도메인: $($z.name)"
    Write-Host "  네임서버 1차: $($z.name_servers[0])"
    Write-Host "  네임서버 2차: $($z.name_servers[1])"
    if ($z.name_servers.Count -gt 2) {
        Write-Host "  네임서버 3차: $($z.name_servers[2])"
    }
    Write-Host "  → 가비아에는 위 2개(또는 표시된 개수)만 등록하면 됨."
    Write-Host ""
}
