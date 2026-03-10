# sswe.co.kr zone에 CNAME 추가 (프론트 → Cloudflare Pages)
# .env 사용. Pages 프로젝트가 academy-frontend.pages.dev 라고 가정.
param(
    [string]$PagesTarget = "academy-frontend.pages.dev"
)

$ErrorActionPreference = 'Stop'
$envFile = Join-Path $PSScriptRoot '..\.env'
if (-not (Test-Path $envFile)) { Write-Host "backend\.env not found."; exit 1 }
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^CLOUDFLARE_(EMAIL|API_KEY|ACCOUNT_ID)=(.+)$') {
        [System.Environment]::SetEnvironmentVariable("CLOUDFLARE_$($matches[1])", $matches[2].Trim(), 'Process')
    }
}
$email = $env:CLOUDFLARE_EMAIL
$key   = $env:CLOUDFLARE_API_KEY
if (-not $email -or -not $key) { Write-Host "CLOUDFLARE_EMAIL, CLOUDFLARE_API_KEY required."; exit 1 }

$headers = @{
    'X-Auth-Email' = $email
    'X-Auth-Key'   = $key
    'Content-Type' = 'application/json'
}

# Get zone id for sswe.co.kr
$listUri = 'https://api.cloudflare.com/client/v4/zones?name=sswe.co.kr'
$list = Invoke-RestMethod -Uri $listUri -Headers $headers -Method Get
if (-not $list.success -or $list.result.Count -eq 0) {
    Write-Host "Zone sswe.co.kr not found."
    exit 1
}
$zoneId = $list.result[0].id
Write-Host "Zone sswe.co.kr id: $zoneId"

$dnsUri = "https://api.cloudflare.com/client/v4/zones/$zoneId/dns_records"

# Apex: @ -> CNAME (Cloudflare CNAME flattening)
$bodyApex = @{ type = "CNAME"; name = "@"; content = $PagesTarget; ttl = 1; proxied = $true } | ConvertTo-Json
try {
    $r1 = Invoke-RestMethod -Uri $dnsUri -Headers $headers -Method Post -Body $bodyApex
    if ($r1.success) { Write-Host "Created @ CNAME -> $PagesTarget" } else { Write-Host "Apex create failed: $($r1.errors)" }
} catch {
    if ($_.Exception.Message -match 'already exists') { Write-Host "Apex CNAME already exists." } else { Write-Host "Apex error: $_" }
}

# www -> CNAME
$bodyWww = @{ type = "CNAME"; name = "www"; content = $PagesTarget; ttl = 1; proxied = $true } | ConvertTo-Json
try {
    $r2 = Invoke-RestMethod -Uri $dnsUri -Headers $headers -Method Post -Body $bodyWww
    if ($r2.success) { Write-Host "Created www CNAME -> $PagesTarget" } else { Write-Host "www create failed: $($r2.errors)" }
} catch {
    if ($_.Exception.Message -match 'already exists') { Write-Host "www CNAME already exists." } else { Write-Host "www error: $_" }
}

Write-Host "Done. sswe.co.kr / www.sswe.co.kr -> $PagesTarget (proxied)."
