# 기존 테넌트 zone(tchul.com) DNS 레코드 조회 — CNAME 타깃 확인용
$ErrorActionPreference = 'Stop'
$envFile = Join-Path $PSScriptRoot '..\.env'
if (-not (Test-Path $envFile)) { Write-Host ".env not found."; exit 1 }
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^CLOUDFLARE_(EMAIL|API_KEY)=(.+)$') {
        [System.Environment]::SetEnvironmentVariable("CLOUDFLARE_$($matches[1])", $matches[2].Trim(), 'Process')
    }
}
$headers = @{
    'X-Auth-Email' = $env:CLOUDFLARE_EMAIL
    'X-Auth-Key'   = $env:CLOUDFLARE_API_KEY
    'Content-Type' = 'application/json'
}
$zoneName = $args[0]
if (-not $zoneName) { $zoneName = "tchul.com" }
$list = Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/zones?name=$zoneName" -Headers $headers -Method Get
if (-not $list.success -or $list.result.Count -eq 0) {
    Write-Host "Zone $zoneName not found."
    exit 1
}
$zoneId = $list.result[0].id
$recs = Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/zones/$zoneId/dns_records" -Headers $headers -Method Get
Write-Host "DNS records for $zoneName (zone_id=$zoneId):"
$recs.result | ForEach-Object { Write-Host "  $($_.type) $($_.name) -> $($_.content) (proxied=$($_.proxied))" }
