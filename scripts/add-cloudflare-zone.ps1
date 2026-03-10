# Cloudflare API로 zone 추가 후 네임서버 반환
# 사용: .\scripts\add-cloudflare-zone.ps1 [-Domain "sswe.co.kr"]
# .env의 CLOUDFLARE_EMAIL, CLOUDFLARE_API_KEY, CLOUDFLARE_ACCOUNT_ID 사용

param([string]$Domain = "sswe.co.kr")

$ErrorActionPreference = 'Stop'
$envFile = Join-Path $PSScriptRoot '..\.env'
if (-not (Test-Path $envFile)) {
    Write-Host "backend\.env not found."
    exit 1
}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^CLOUDFLARE_(EMAIL|API_KEY|ACCOUNT_ID)=(.+)$') {
        [System.Environment]::SetEnvironmentVariable("CLOUDFLARE_$($matches[1])", $matches[2].Trim(), 'Process')
    }
}
$email   = $env:CLOUDFLARE_EMAIL
$key     = $env:CLOUDFLARE_API_KEY
$account = $env:CLOUDFLARE_ACCOUNT_ID
if (-not $email -or -not $key -or -not $account) {
    Write-Host "CLOUDFLARE_EMAIL, CLOUDFLARE_API_KEY, CLOUDFLARE_ACCOUNT_ID must be set in backend\.env"
    exit 1
}

$headers = @{
    'X-Auth-Email' = $email
    'X-Auth-Key'   = $key
    'Content-Type'  = 'application/json'
}
$body = @{
    name        = $Domain
    account     = @{ id = $account }
    jump_start  = $true
    type        = 'full'
} | ConvertTo-Json

$uri = 'https://api.cloudflare.com/client/v4/zones'
try {
    $r = Invoke-RestMethod -Uri $uri -Headers $headers -Method Post -Body $body
} catch {
    $ex = $_.Exception
    if ($ex.Response) {
        $reader = New-Object System.IO.StreamReader($ex.Response.GetResponseStream())
        $reader.BaseStream.Position = 0
        $errBody = $reader.ReadToEnd()
        Write-Host "API error response: $errBody"
    } else {
        Write-Host "API error: $ex"
    }
    exit 1
}

if (-not $r.success) {
    Write-Host "API success=false. errors: $($r.errors | ConvertTo-Json -Compress)"
    exit 1
}

$z = $r.result
Write-Host ""
Write-Host "=== $Domain added. Use these in Gabia (네임서버 설정) ==="
Write-Host ""
Write-Host "  1차: $($z.name_servers[0])"
Write-Host "  2차: $($z.name_servers[1])"
Write-Host ""
Write-Host "Copy the two lines above into Gabia domain nameserver settings."
Write-Host ""
