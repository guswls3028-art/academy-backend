# ===============================
# HakwonPlus DEV - Single Terminal (Academy Local Dev)
# ===============================
# 백엔드 + 프론트(+ 터널) 한 터미널에서 Job으로 실행

$ErrorActionPreference = "Continue"

# 스크립트 위치 기준 경로 (바탕화면 바로가기에서도 동작)
$AcademyRoot = $PSScriptRoot
$FrontRoot   = Join-Path (Split-Path $PSScriptRoot -Parent) "frontend"

if (-not (Test-Path $FrontRoot)) {
  Write-Host "프론트 폴더를 찾을 수 없습니다: $FrontRoot" -ForegroundColor Red
  Read-Host 'Press Enter to close'
  exit 1
}

$Host.UI.RawUI.WindowTitle = "Academy Local Dev (Backend + Frontend)"

# 백엔드: 표준 .venv를 우선 사용하고, 기존 venv는 호환 경로로만 허용한다.
# Job 안에서 Activate 대신 interpreter를 직접 실행해 shell별 활성화 차이를 없앤다.
$pythonCandidates = @(
  (Join-Path $AcademyRoot ".venv\Scripts\python.exe"),
  (Join-Path $AcademyRoot "venv\Scripts\python.exe")
)
$pythonExe = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $pythonExe) {
  $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
  if (-not $pythonCommand) {
    throw "Python 3.11 환경이 없습니다. py -3.11 -m venv .venv 후 requirements를 설치하세요."
  }
  $pythonExe = $pythonCommand.Source
}

$pythonVersion = & $pythonExe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $pythonVersion.Trim() -ne "3.11") {
  throw "Academy backend requires Python 3.11; resolved '$pythonExe' as $pythonVersion."
}

if (-not (Get-Command pnpm -ErrorAction SilentlyContinue)) {
  throw "pnpm이 없습니다. frontend/package.json의 packageManager 버전을 설치하세요."
}

# 백엔드 Job
$backendJob = Start-Job -Name Backend -ScriptBlock {
  param($root, $py)
  Set-Location $root
  & $py manage.py runserver 0.0.0.0:8000 2>&1
} -ArgumentList $AcademyRoot, $pythonExe

# 프론트엔드 Job
$frontendJob = Start-Job -Name Frontend -ScriptBlock {
  param($root)
  Set-Location $root
  pnpm dev -- --host 127.0.0.1 --port 5174 2>&1
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
