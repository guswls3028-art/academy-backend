# 1) sswe.co.kr zone에서 수동 CNAME 제거 (1014 방지)
# 2) Pages 프로젝트 academy-frontend에 커스텀 도메인 추가 (다른 테넌트와 동일)
$ErrorActionPreference = 'Stop'
$envFile = Join-Path $PSScriptRoot '..\.env'
if (-not (Test-Path $envFile)) { Write-Host "backend\.env not found."; exit 1 }
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^CLOUDFLARE_(EMAIL|API_KEY|ACCOUNT_ID)=(.+)$') {
        [System.Environment]::SetEnvironmentVariable("CLOUDFLARE_$($matches[1])", $matches[2].Trim(), 'Process')
    }
}
$email    = $env:CLOUDFLARE_EMAIL
$key      = $env:CLOUDFLARE_API_KEY
$accountId = $env:CLOUDFLARE_ACCOUNT_ID
if (-not $email -or -not $key -or -not $accountId) { Write-Host "CLOUDFLARE_EMAIL, API_KEY, ACCOUNT_ID required."; exit 1 }

$headers = @{
    'X-Auth-Email' = $email
    'X-Auth-Key'   = $key
    'Content-Type' = 'application/json'
}

# --- 1) sswe.co.kr zone DNS: @, www CNAME 삭제 ---
$list = Invoke-RestMethod -Uri 'https://api.cloudflare.com/client/v4/zones?name=sswe.co.kr' -Headers $headers -Method Get
if (-not $list.success -or $list.result.Count -eq 0) { Write-Host "Zone sswe.co.kr not found."; exit 1 }
$zoneId = $list.result[0].id
$dnsUri = "https://api.cloudflare.com/client/v4/zones/$zoneId/dns_records"
$recs   = Invoke-RestMethod -Uri $dnsUri -Headers $headers -Method Get
foreach ($r in $recs.result) {
    if (($r.type -eq 'CNAME') -and (($r.name -eq '@' -or $r.name -eq 'sswe.co.kr' -or $r.name -eq 'www'))) {
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
foreach ($domain in @('sswe.co.kr', 'www.sswe.co.kr')) {
    $body = @{ name = $domain } | ConvertTo-Json
    try {
        $res = Invoke-RestMethod -Uri "$base/domains" -Headers $headers -Method Post -Body $body
        if ($res.success) {
            Write-Host "Pages custom domain added: $domain"
        } else {
            Write-Host "Pages add $domain failed: $($res.errors | ConvertTo-Json -Compress)"
        }
    } catch {
        $ex = $_.Exception
        if ($ex.Response) {
            $stream = $ex.Response.GetResponseStream()
            $reader = New-Object System.IO.StreamReader($stream)
            $reader.BaseStream.Position = 0
            $errBody = $reader.ReadToEnd()
            Write-Host "Pages API error for $domain : $errBody"
        } else {
            Write-Host "Pages API error for $domain : $ex"
        }
    }
}
Write-Host "Done. If Pages API returned 404/4xx, add sswe.co.kr and www.sswe.co.kr manually in Workers & Pages > academy-frontend > Custom domains."
