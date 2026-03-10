# Update one DNS record by name (CNAME content)
param([string]$ZoneName, [string]$RecordName, [string]$NewContent)
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
$recs = Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/zones/$zid/dns_records?name=$RecordName" -Headers $h -Method Get
if (-not $recs.result -or $recs.result.Count -eq 0) { Write-Host "Record not found"; exit 1 }
$rid = $recs.result[0].id
$body = @{ type = $recs.result[0].type; name = $RecordName; content = $NewContent; ttl = 1; proxied = $true } | ConvertTo-Json
$r = Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/zones/$zid/dns_records/$rid" -Headers $h -Method Put -Body $body
if ($r.success) { Write-Host "Updated $RecordName -> $NewContent" } else { Write-Host $r.errors }
