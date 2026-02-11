# ===============================
# HakwonPlus DEV One-Click Runner
# ===============================

$ErrorActionPreference = "Stop"

# 1. Backend (Django) - 외부 접근 허용
Start-Process powershell -ArgumentList @(
  "-NoExit",
  "-Command",
  "cd C:\academy; .\venv\Scripts\activate; python manage.py runserver 0.0.0.0:8000"
)

# 2. Frontend (Vite) - cloudflared 대응 (IPv4 + IPv6)
Start-Process powershell -ArgumentList @(
  "-NoExit",
  "-Command",
  "cd C:\academyfront; pnpm dev -- --host 0.0.0.0 --port 5174"
)

# 3. Cloudflare Tunnel (Named Tunnel)
Start-Process powershell -ArgumentList @(
  "-NoExit",
  "-Command",
  "cloudflared tunnel run dev-pc"
)

Write-Host '🚀 DEV environment started (Backend + Frontend + Tunnel)'
