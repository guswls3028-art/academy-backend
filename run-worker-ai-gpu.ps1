# ===============================
# AI Worker GPU - SQS 기반 (한 터미널에서 실행)
# ===============================
$ErrorActionPreference = "Continue"
$AcademyRoot = "C:\academy"

Set-Location $AcademyRoot
& "$AcademyRoot\venv\Scripts\Activate.ps1"
Write-Host 'AI Worker (GPU) starting (SQS Long Polling). Ctrl+C to stop.' -ForegroundColor Cyan
python -m apps.worker.ai_worker.sqs_main_gpu
Read-Host 'Worker ended - Press Enter to close'
