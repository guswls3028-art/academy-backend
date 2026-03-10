# Zone DNS records list (for debugging)
param([string]$ZoneName = "tchul.com")
$ErrorActionPreference = 'Stop'
$envFile = Join-Path $PSScriptRoot '..\.env'
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^CLOUDFLARE_(EMAIL|API_KEY)=(.+)$') {
        [System.Environment]::SetEnvironmentVariable("CLOUDFLARE_$($matches[1])", $matches[2].Trim(), 'Process')
    }
}
$h = @{ 'X-Auth-Email' = $env:CLOUDFLARE_EMAIL; 'X-Auth-Key' = $env:CLOUDFLARE_API_KEY; 'Content-Type' = 'application/json' }
$list = Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/zones?name=$ZoneName" -Headers $h -Method Get
if (-not $list.success -or $list.result.Count -eq 0) { Write-Host "Zone not found"; exit 1 }
$zid = $list.result[0].id
$recs = Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/zones/$zid/dns_records" -Headers $h -Method Get
Write-Host "Zone $ZoneName ($zid):"
$recs.result | ForEach-Object { Write-Host "  $($_.type) name=$($_.name) content=$($_.content) proxied=$($_.proxied)" }
