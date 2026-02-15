# ===============================
# HakwonPlus DEV - Single Terminal
# ===============================
# 백엔드 + 프론트 + 터널을 한 터미널에서 Job으로 실행 (출력은 섞여서 나옴)

$ErrorActionPreference = "Continue"

$AcademyRoot = "C:\academy"
$FrontRoot  = "C:\academyfront"

# 백엔드 Job (stderr -> stdout 합쳐서 RemoteException/NativeCommandError 방지)
$backendJob = Start-Job -Name Backend -ScriptBlock {
  Set-Location $using:AcademyRoot
  & "$using:AcademyRoot\venv\Scripts\Activate.ps1"
  python manage.py runserver 0.0.0.0:8000 2>&1
}

# 프론트엔드 Job (다른 워킹디렉터리)
$frontendJob = Start-Job -Name Frontend -ScriptBlock {
  Set-Location $using:FrontRoot
  pnpm dev -- --host 0.0.0.0 --port 5174 2>&1
}

# 터널 Job
$tunnelJob = Start-Job -Name Tunnel -ScriptBlock {
  cloudflared tunnel run dev-pc 2>&1
}

Write-Host 'DEV started in one terminal (Backend + Frontend + Tunnel). Output mixed below. Ctrl+C to stop all.' -ForegroundColor Green
Write-Host ''

try {
  while ($true) {
    foreach ($job in @($backendJob, $frontendJob, $tunnelJob)) {
      $out = Receive-Job -Job $job
      if ($out) {
        $tag = switch ($job.Name) { Backend { 'B' } Frontend { 'F' } Tunnel { 'T' } }
        foreach ($line in ($out -split "`n")) {
          if ($line.Trim() -ne '') { Write-Host "[$tag] $line" }
        }
      }
      if ($job.State -eq 'Failed') {
        Write-Host "[$($job.Name)] Job failed." -ForegroundColor Red
      }
    }
    $running = @($backendJob, $frontendJob, $tunnelJob) | Where-Object { $_.State -eq 'Running' }
    if ($running.Count -eq 0) { break }
    Start-Sleep -Milliseconds 500
  }
}
finally {
  Stop-Job -Job $backendJob, $frontendJob, $tunnelJob -ErrorAction SilentlyContinue
  Remove-Job -Job $backendJob, $frontendJob, $tunnelJob -Force -ErrorAction SilentlyContinue
  Write-Host 'All jobs stopped.' -ForegroundColor Yellow
}

Read-Host 'Press Enter to close'
