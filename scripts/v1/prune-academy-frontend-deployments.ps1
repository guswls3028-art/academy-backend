# Cloudflare Pages: academy-frontend 프로젝트에서 최신 배포 1개만 남기고 나머지 삭제
# .env에서 Cloudflare 인증 사용
$ErrorActionPreference = "Stop"
$envPath = Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) ".env"
if (-not (Test-Path $envPath)) { Write-Error "backend\.env not found"; exit 1 }

$vars = @{}
Get-Content $envPath | Where-Object { $_ -match '^CLOUDFLARE_(EMAIL|API_KEY|ACCOUNT_ID)=' } | ForEach-Object {
  if ($_ -match '^([^=]+)=(.+)$') {
    $vars[$matches[1]] = $matches[2].Trim().Trim('"')
  }
}
$email = $vars["CLOUDFLARE_EMAIL"]
$key   = $vars["CLOUDFLARE_API_KEY"]
$accountId = $vars["CLOUDFLARE_ACCOUNT_ID"]
if (-not $accountId) { Write-Error "CLOUDFLARE_ACCOUNT_ID missing"; exit 1 }

$projectName = "academy-frontend"
$base = "https://api.cloudflare.com/client/v4/accounts/$accountId/pages/projects/$projectName"
$headers = @{
  "X-Auth-Email" = $email
  "X-Auth-Key"   = $key
  "Content-Type" = "application/json"
}

$listUrl = "$base/deployments"
$retries = 0
do {
  try {
    $res = Invoke-RestMethod -Uri $listUrl -Method Get -Headers $headers
    break
  } catch {
    $msg = $_.Exception.Message; if ($_.ErrorDetails.Message) { $msg += " " + $_.ErrorDetails.Message }
    if ($retries -lt 8) {
      $retries++
      Write-Host "List failed (throttle?), waiting 90s then retry $retries/8..."
      Start-Sleep -Seconds 90
    } else {
      Write-Host "List deployments failed: $msg"
      exit 1
    }
  }
} while ($true)

if (-not $res.result -or $res.result.Count -eq 0) {
  Write-Host "No deployments found."
  exit 0
}

# API는 최신순으로 반환한다고 가정. 첫 번째 = 최신 → 유지, 나머지 삭제
$all = @($res.result)
$keepId = $all[0].id
$totalCount = $res.result_info.total_count
Write-Host "Keeping latest deployment: $keepId (total deployments: $totalCount)"

$totalDeleted = 0
$firstPage = $true

do {
  $retries = 0
  do {
    try {
      $res = Invoke-RestMethod -Uri $listUrl -Method Get -Headers $headers
      break
    } catch {
      $msg = $_.Exception.Message; if ($_.ErrorDetails.Message) { $msg += " " + $_.ErrorDetails.Message }
      if ($retries -lt 8) {
        $retries++
        Write-Host "List failed, waiting 90s then retry $retries/8..."
        Start-Sleep -Seconds 90
      } else {
        Write-Host "List failed: $msg"
        exit 1
      }
    }
  } while ($true)

  $deployments = @($res.result)
  if ($deployments.Count -eq 0) { break }

  $remaining = $res.result_info.total_count
  if ($remaining -le 50) {
    Write-Host "Remaining deployments: $remaining (<=50). Stopping."
    break
  }

  foreach ($d in $deployments) {
    $id = $d.id
    if (-not $id) { continue }
    if ($id -eq $keepId) { continue }
    $delUrl = "$base/deployments/$id`?force=true"
    try {
      Invoke-RestMethod -Uri $delUrl -Method Delete -Headers $headers | Out-Null
      $totalDeleted++
      if ($totalDeleted % 50 -eq 0) { Write-Host "Deleted $totalDeleted so far..." }
      Start-Sleep -Milliseconds 150
    } catch {
      $errMsg = $_.Exception.Message; if ($_.ErrorDetails.Message) { $errMsg += " " + $_.ErrorDetails.Message }
      if ($errMsg -match "Rate limit|971|10429") {
        Write-Host "Rate limited, waiting 60s..."
        Start-Sleep -Seconds 60
      }
    }
  }
} while ($true)

Write-Host "Done. Deleted $totalDeleted old deployments. Kept latest: $keepId (remaining <= 50)"
