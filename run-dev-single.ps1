# ===============================
# HakwonPlus DEV - Single Terminal (Academy Local Dev)
# ===============================
# 백엔드 + 프론트(+ 터널) 한 터미널에서 Job으로 실행

$ErrorActionPreference = "Continue"

# 스크립트 위치 기준 경로 (바탕화면 바로가기에서도 동작)
$AcademyRoot = $PSScriptRoot
$FrontRoot   = Join-Path (Split-Path $PSScriptRoot -Parent) "academyfront"

if (-not (Test-Path $FrontRoot)) {
  Write-Host "프론트 폴더를 찾을 수 없습니다: $FrontRoot" -ForegroundColor Red
  Read-Host 'Press Enter to close'
  exit 1
}

$Host.UI.RawUI.WindowTitle = "Academy Local Dev (Backend + Frontend)"

# 백엔드: venv python 직접 사용 (Job 내에서 Activate 불안정 방지)
$pythonExe = Join-Path $AcademyRoot "venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) { $pythonExe = "python" }

# 백엔드 Job
$backendJob = Start-Job -Name Backend -ScriptBlock {
  param($root, $py)
  Set-Location $root
  if ($py -ne "python") { & $py manage.py runserver 0.0.0.0:8000 2>&1 }
  else { python manage.py runserver 0.0.0.0:8000 2>&1 }
} -ArgumentList $AcademyRoot, $pythonExe

# 프론트엔드 Job
$frontendJob = Start-Job -Name Frontend -ScriptBlock {
  param($root)
  Set-Location $root
  pnpm dev -- --host 0.0.0.0 --port 5174 2>&1
} -ArgumentList $FrontRoot

# 터널 Job (cloudflared 없으면 스킵)
$tunnelJob = $null
if (Get-Command cloudflared -ErrorAction SilentlyContinue) {
  $tunnelJob = Start-Job -Name Tunnel -ScriptBlock { cloudflared tunnel run dev-pc 2>&1 }
}

Write-Host '========================================' -ForegroundColor Cyan
Write-Host '  Academy Local Dev (Backend + Frontend)' -ForegroundColor Cyan
Write-Host '========================================' -ForegroundColor Cyan
Write-Host ''
Write-Host 'Backend:  http://localhost:8000' -ForegroundColor Yellow
Write-Host 'Frontend: http://localhost:5174  (9999: /login/9999)' -ForegroundColor Yellow
Write-Host ''
Write-Host 'Ctrl+C 로 모두 종료' -ForegroundColor Gray
Write-Host ''

$jobs = @($backendJob, $frontendJob)
if ($tunnelJob) { $jobs += $tunnelJob }

try {
  while ($true) {
    foreach ($job in $jobs) {
      $out = Receive-Job -Job $job -ErrorAction SilentlyContinue
      if ($out) {
        $tag = switch ($job.Name) { Backend { 'B' } Frontend { 'F' } Tunnel { 'T' } default { '?' } }
        foreach ($line in ($out -split "`n")) {
          if ($line.Trim() -ne '') { Write-Host "[$tag] $line" }
        }
      }
      if ($job.State -eq 'Failed') {
        Write-Host "[$($job.Name)] Job failed." -ForegroundColor Red
      }
    }
    $running = $jobs | Where-Object { $_.State -eq 'Running' }
    if ($running.Count -eq 0) { break }
    Start-Sleep -Milliseconds 500
  }
}
finally {
  Stop-Job -Job $jobs -ErrorAction SilentlyContinue
  Remove-Job -Job $jobs -Force -ErrorAction SilentlyContinue
  Write-Host 'All jobs stopped.' -ForegroundColor Yellow
}

Read-Host 'Press Enter to close'
