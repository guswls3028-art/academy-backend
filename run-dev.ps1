# ===============================
# HakwonPlus DEV One-Click Runner
# ===============================
# 더블클릭: run-dev.bat 사용 권장 (에러 시 창 유지 + 실행 정책 우회)

$ErrorActionPreference = "Continue"

$AcademyRoot = "C:\academy"
$FrontRoot  = "C:\academyfront"

$cmdBackend = "Set-Location '$AcademyRoot'; & '$AcademyRoot\venv\Scripts\Activate.ps1'; python manage.py runserver 0.0.0.0:8000; Read-Host `"Backend ended - Press Enter to close`""
$cmdFront   = "pnpm dev -- --host 0.0.0.0 --port 5174; Read-Host `"Frontend ended - Press Enter to close`""
$cmdTunnel  = "cloudflared tunnel run dev-pc; Read-Host `"Tunnel ended - Press Enter to close`""

try {
  # 1. Backend (Django)
  Start-Process powershell -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $cmdBackend

  # 2. Frontend (Vite)
  Start-Process powershell -WorkingDirectory $FrontRoot -ArgumentList "-NoExit", "-Command", $cmdFront

  # 3. Cloudflare Tunnel
  Start-Process powershell -ArgumentList "-NoExit", "-Command", $cmdTunnel

  Write-Host 'DEV environment started (Backend + Frontend + Tunnel)' -ForegroundColor Green
}
catch {
  Write-Host 'ERROR:' -ForegroundColor Red
  Write-Host $_.Exception.Message -ForegroundColor Red
  Write-Host $_.ScriptStackTrace
}

Read-Host 'Press Enter to close this window'
