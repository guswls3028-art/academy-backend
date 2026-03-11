# Pages 프로젝트 academy-frontend에 커스텀 도메인 추가 + zone에 CNAME 추가 (1014·1016 방지)
# 사용: .\scripts\pages-add-custom-domain.ps1 -Domain "newdomain.co.kr"
# 1) zone에서 잘못된 CNAME 제거 2) Pages에 커스텀 도메인 등록 3) zone에 academy-frontend-26b.pages.dev 로 CNAME 추가

param(
    [Parameter(Mandatory = $true)]
    [string]$Domain
)

$Domain = $Domain.Trim().ToLower()
if (-not $Domain) { Write-Host "Usage: .\pages-add-custom-domain.ps1 -Domain newdomain.co.kr"; exit 1 }

# Pages 실제 타깃 (다른 테넌트와 동일). academy-frontend.pages.dev 가 아님.
$PagesCnameTarget = "academy-frontend-26b.pages.dev"

$ErrorActionPreference = 'Stop'
$envFile = Join-Path $PSScriptRoot '..\.env'
if (-not (Test-Path $envFile)) { Write-Host "backend\.env not found."; exit 1 }
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^CLOUDFLARE_(EMAIL|API_KEY|ACCOUNT_ID)=(.+)$') {
        [System.Environment]::SetEnvironmentVariable("CLOUDFLARE_$($matches[1])", $matches[2].Trim(), 'Process')
    }
}
$email     = $env:CLOUDFLARE_EMAIL
$key       = $env:CLOUDFLARE_API_KEY
$accountId = $env:CLOUDFLARE_ACCOUNT_ID
if (-not $email -or -not $key -or -not $accountId) { Write-Host "CLOUDFLARE_EMAIL, API_KEY, ACCOUNT_ID required."; exit 1 }

$headers = @{
    'X-Auth-Email' = $email
    'X-Auth-Key'   = $key
    'Content-Type' = 'application/json'
}

# --- 1) 해당 zone에서 @ / apex / www CNAME 삭제 (잘못된 타깃 정리) ---
$list = Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/zones?name=$Domain" -Headers $headers -Method Get
if (-not $list.success -or $list.result.Count -eq 0) {
    Write-Host "Zone $Domain not found. Run add-cloudflare-zone.ps1 -Domain $Domain first."
    exit 1
}
$zoneId = $list.result[0].id
$dnsUri = "https://api.cloudflare.com/client/v4/zones/$zoneId/dns_records"
$recs   = Invoke-RestMethod -Uri $dnsUri -Headers $headers -Method Get
foreach ($r in $recs.result) {
    if (($r.type -eq 'CNAME') -and (($r.name -eq '@' -or $r.name -eq $Domain -or $r.name -eq 'www'))) {
        $delUri = "$dnsUri/$($r.id)"
        try {
            Invoke-RestMethod -Uri $delUri -Headers $headers -Method Delete | Out-Null
            Write-Host "Deleted DNS: $($r.type) $($r.name) -> $($r.content)"
        } catch { Write-Host "Delete $($r.name) failed: $_" }
    }
}

# --- 2) Pages: academy-frontend에 커스텀 도메인 추가 ---
$projectName = 'academy-frontend'
$base = "https://api.cloudflare.com/client/v4/accounts/$accountId/pages/projects/$projectName"
$wwwDomain = if ($Domain.StartsWith('www.')) { $Domain } else { "www.$Domain" }
foreach ($d in @($Domain, $wwwDomain)) {
    $body = @{ name = $d } | ConvertTo-Json
    try {
        $res = Invoke-RestMethod -Uri "$base/domains" -Headers $headers -Method Post -Body $body
        if ($res.success) {
            Write-Host "Pages custom domain added: $d"
        } else {
            Write-Host "Pages add $d failed: $($res.errors | ConvertTo-Json -Compress)"
        }
    } catch {
        $ex = $_.Exception
        if ($ex.Response) {
            $stream = $ex.Response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            $reader.BaseStream.Position = 0
            $errBody = $reader.ReadToEnd()
            Write-Host "Pages API error for $d : $errBody"
        } else {
            Write-Host "Pages API error for $d : $ex"
        }
    }
}

# --- 3) zone에 CNAME 추가 (1016 방지). 타깃은 academy-frontend-26b.pages.dev ---
foreach ($rec in @(
    @{ type = "CNAME"; name = "@"; content = $PagesCnameTarget; ttl = 1; proxied = $true },
    @{ type = "CNAME"; name = "www"; content = $PagesCnameTarget; ttl = 1; proxied = $true }
)) {
    $body = $rec | ConvertTo-Json
    try {
        $r = Invoke-RestMethod -Uri $dnsUri -Headers $headers -Method Post -Body $body
        if ($r.success) { Write-Host "Created CNAME $($rec.name) -> $($rec.content)" }
        else { Write-Host "CNAME $($rec.name) failed: $($r.errors)" }
    } catch {
        if ($_.Exception.Message -match 'already exists') { Write-Host "CNAME $($rec.name) already exists" }
        else { Write-Host "CNAME $($rec.name) error: $_" }
    }
}

Write-Host "Done. Verify: curl -sI -o NUL -w '%{http_code}' https://$Domain"
