# Check worker images do not contain API-only packages
# Run: .\scripts\check_worker_deps.ps1
# Or: docker run --rm academy-video-worker:latest pip freeze | Select-String "djangorestframework|gunicorn|drf-yasg"

$images = @("academy-video-worker:latest", "academy-ai-worker:latest", "academy-messaging-worker:latest")
$forbidden = @("djangorestframework", "gunicorn", "drf-yasg", "gevent")

$allOk = $true
foreach ($img in $images) {
    Write-Host "Checking $img..." -ForegroundColor Yellow
    $imgOk = $true
    $freeze = docker run --rm $img pip freeze 2>$null
    foreach ($pkg in $forbidden) {
        if ($freeze -match $pkg) {
            Write-Host "  FAIL: $pkg found in $img" -ForegroundColor Red
            $imgOk = $false
            $allOk = $false
        }
    }
    if ($imgOk) {
        Write-Host "  OK" -ForegroundColor Green
    }
}

if (-not $allOk) {
    exit 1
}
exit 0
