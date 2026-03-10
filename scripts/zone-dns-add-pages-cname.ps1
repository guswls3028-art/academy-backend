# Zone에 Pages CNAME 추가 (1016 해결용). 타깃은 기존 테넌트와 동일: academy-frontend-26b.pages.dev
param(
    [Parameter(Mandatory = $true)]
    [string]$Domain,
    [string]$PagesTarget = "academy-frontend-26b.pages.dev"
)
$Domain = $Domain.Trim().ToLower()
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
if (-not $email -or -not $key) { Write-Host "CLOUDFLARE_EMAIL, API_KEY required."; exit 1 }
$headers = @{ 'X-Auth-Email' = $email; 'X-Auth-Key' = $key; 'Content-Type' = 'application/json' }
$list = Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/zones?name=$Domain" -Headers $headers -Method Get
if (-not $list.success -or $list.result.Count -eq 0) { Write-Host "Zone $Domain not found."; exit 1 }
$zoneId = $list.result[0].id
$dnsUri = "https://api.cloudflare.com/client/v4/zones/$zoneId/dns_records"
foreach ($rec in @(
    @{ type = "CNAME"; name = "@"; content = $PagesTarget; ttl = 1; proxied = $true },
    @{ type = "CNAME"; name = "www"; content = $PagesTarget; ttl = 1; proxied = $true }
)) {
    $body = $rec | ConvertTo-Json
    try {
        $r = Invoke-RestMethod -Uri $dnsUri -Headers $headers -Method Post -Body $body
        if ($r.success) { Write-Host "Created $($rec.type) $($rec.name) -> $($rec.content)" }
        else { Write-Host "Failed $($rec.name): $($r.errors)" }
    } catch {
        if ($_.Exception.Message -match 'already exists') { Write-Host "Exists: $($rec.name)" }
        else { Write-Host "Error $($rec.name): $_" }
    }
}
Write-Host "Done. $Domain / www.$Domain -> $PagesTarget (proxied)"
