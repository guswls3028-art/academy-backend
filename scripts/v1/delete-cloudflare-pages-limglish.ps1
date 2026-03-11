# Cloudflare Pages: limglish 프로젝트의 배포 전부 삭제 후 프로젝트 삭제
# .env에서 읽어서 사용 (비밀 출력 금지)
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

$base = "https://api.cloudflare.com/client/v4/accounts/$accountId/pages/projects/limglish"
$headers = @{
  "X-Auth-Email" = $email
  "X-Auth-Key"   = $key
  "Content-Type" = "application/json"
}

# 1) 배포 목록 조회(1페이지) 후 전부 삭제, 반복 until 빈 목록
$totalDeleted = 0
do {
  $listUrl = "$base/deployments"
  $retries = 0
  do {
    try {
      $res = Invoke-RestMethod -Uri $listUrl -Method Get -Headers $headers
      break
    } catch {
      $msg = $_.Exception.Message + (if ($_.ErrorDetails.Message) { $_.ErrorDetails.Message } else { "" })
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

  if (-not $res.result) { break }
  $deployments = @($res.result)
  if ($deployments.Count -eq 0) { break }

  foreach ($d in $deployments) {
    $id = $d.id
    if (-not $id) { continue }
    $delUrl = "$base/deployments/$id`?force=true"
    try {
      Invoke-RestMethod -Uri $delUrl -Method Delete -Headers $headers | Out-Null
      $totalDeleted++
      if ($totalDeleted % 50 -eq 0) { Write-Host "Deleted $totalDeleted so far..." }
      Start-Sleep -Milliseconds 150
    } catch {
      if ($_.Exception.Message -match "Rate limit|971|10429") {
        Write-Host "Rate limited on delete, waiting 60s..."
        Start-Sleep -Seconds 60
      }
    }
  }
} while ($deployments.Count -gt 0)

Write-Host "Total deployments deleted: $totalDeleted"

# 2) 프로젝트 삭제
try {
  Invoke-RestMethod -Uri $base -Method Delete -Headers $headers | Out-Null
  Write-Host "Project limglish deleted."
} catch {
  Write-Host "Delete project failed: $_"
  exit 1
}
