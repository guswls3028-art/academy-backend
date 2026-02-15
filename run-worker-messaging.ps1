# ===============================
# Messaging Worker - SQS 기반 (한 터미널에서 실행)
# ===============================
$ErrorActionPreference = "Continue"
$AcademyRoot = "C:\academy"

Set-Location $AcademyRoot
& "$AcademyRoot\venv\Scripts\Activate.ps1"
Write-Host 'Messaging Worker starting (SQS Long Polling). Ctrl+C to stop.' -ForegroundColor Cyan
python -m apps.worker.messaging_worker.sqs_main
Read-Host 'Worker ended - Press Enter to close'
