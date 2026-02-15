# ===============================
# Workers - Single Terminal (AI CPU + Video + Messaging)
# ===============================
# 세 워커를 한 터미널에서 Job으로 실행 (출력은 [A][V][M] 섞여서 나옴)
# Job은 현재 프로세스 환경을 상속하므로, 스크립트 시작 시 .env 로드 + DJANGO_SETTINGS_MODULE 설정

$ErrorActionPreference = "Continue"

$AcademyRoot = "C:\academy"

# .env + .env.local 로드 (로컬용: .env.local이 있으면 그걸로 덮어씀)
$loadEnv = {
  param($path)
  if (Test-Path $path) {
    Get-Content $path -Encoding UTF8 | ForEach-Object {
      $line = $_.Trim()
      if ($line -and -not $line.StartsWith("#")) {
        $idx = $line.IndexOf("=")
        if ($idx -gt 0) {
          $key = $line.Substring(0, $idx).Trim()
          $val = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
          Set-Item -Path "Env:$key" -Value $val -ErrorAction SilentlyContinue
        }
      }
    }
  }
}
& $loadEnv (Join-Path $AcademyRoot ".env")
& $loadEnv (Join-Path $AcademyRoot ".env.local")

# 워커 전용 Django 설정 (Video/AI가 Django 모델 쓰려면 필수)
$env:DJANGO_SETTINGS_MODULE = "apps.api.config.settings.worker"

# AI Worker (CPU) Job
$aiJob = Start-Job -Name AI -ScriptBlock {
  Set-Location $using:AcademyRoot
  & "$using:AcademyRoot\venv\Scripts\Activate.ps1"
  python -m apps.worker.ai_worker.sqs_main_cpu 2>&1
}

# Video Worker Job
$videoJob = Start-Job -Name Video -ScriptBlock {
  Set-Location $using:AcademyRoot
  & "$using:AcademyRoot\venv\Scripts\Activate.ps1"
  python -m apps.worker.video_worker.sqs_main 2>&1
}

# Messaging Worker Job
$messagingJob = Start-Job -Name Messaging -ScriptBlock {
  Set-Location $using:AcademyRoot
  & "$using:AcademyRoot\venv\Scripts\Activate.ps1"
  python -m apps.worker.messaging_worker.sqs_main 2>&1
}

Write-Host 'Workers started in one terminal (AI CPU + Video + Messaging). Output mixed below. Ctrl+C to stop all.' -ForegroundColor Green
Write-Host '[A]=AI  [V]=Video  [M]=Messaging' -ForegroundColor Gray
Write-Host ''

try {
  while ($true) {
    foreach ($job in @($aiJob, $videoJob, $messagingJob)) {
      $out = Receive-Job -Job $job
      if ($out) {
        $tag = switch ($job.Name) { AI { 'A' } Video { 'V' } Messaging { 'M' } default { '?' } }
        foreach ($line in ($out -split "`n")) {
          if ($line.Trim() -ne '') { Write-Host "[$tag] $line" }
        }
      }
      if ($job.State -eq 'Failed') {
        Write-Host "[$($job.Name)] Job failed." -ForegroundColor Red
      }
    }
    $running = @($aiJob, $videoJob, $messagingJob) | Where-Object { $_.State -eq 'Running' }
    if ($running.Count -eq 0) { break }
    Start-Sleep -Milliseconds 500
  }
}
finally {
  Stop-Job -Job $aiJob, $videoJob, $messagingJob -ErrorAction SilentlyContinue
  Remove-Job -Job $aiJob, $videoJob, $messagingJob -Force -ErrorAction SilentlyContinue
  Write-Host 'All workers stopped.' -ForegroundColor Yellow
}

Read-Host 'Press Enter to close'
