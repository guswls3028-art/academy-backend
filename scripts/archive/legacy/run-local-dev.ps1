# Academy Local Dev - Backend + Frontend in one terminal
$Host.UI.RawUI.WindowTitle = "Academy Local Dev (Backend + Frontend)"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Academy Local Development" -ForegroundColor Cyan
Write-Host "  Backend + Frontend" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Kill existing backend/frontend processes on ports 8000, 5174, 5175, 5176
Write-Host "[CLEANUP] Stopping any process on ports 8000, 5174, 5175, 5176..." -ForegroundColor Yellow
$portsToFree = @(8000, 5174, 5175, 5176)
foreach ($port in $portsToFree) {
    try {
        $conns = Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue
        foreach ($c in $conns) {
            if ($c.OwningProcess) {
                Stop-Process -Id $c.OwningProcess -Force -ErrorAction SilentlyContinue
                Write-Host "  Port $port : stopped PID $($c.OwningProcess)" -ForegroundColor Gray
            }
        }
    } catch { }
}
Start-Sleep -Seconds 1
Write-Host ""

# Check venv Python path
$pythonPath = "python"
if (Test-Path "C:\academy\venv\Scripts\python.exe") {
    $pythonPath = "C:\academy\venv\Scripts\python.exe"
}

# Run Backend as Job (with output)
Write-Host "[BACKEND] Starting..." -ForegroundColor Green
$backendJob = Start-Job -ScriptBlock {
    Set-Location "C:\academy"
    if (Test-Path "venv\Scripts\Activate.ps1") {
        & "venv\Scripts\Activate.ps1"
    }
    python manage.py runserver 0.0.0.0:8000 2>&1
}

# Brief wait
Start-Sleep -Seconds 3

# Run Frontend as Job
Write-Host "[FRONTEND] Starting..." -ForegroundColor Green
Set-Location "C:\academyfront"
$frontendJob = Start-Job -ScriptBlock {
    Set-Location "C:\academyfront"
    pnpm dev 2>&1
}

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Servers running" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Backend:  http://localhost:8000" -ForegroundColor Yellow
Write-Host "Frontend: http://localhost:5174" -ForegroundColor Yellow
Write-Host ""
Write-Host "Press Ctrl+C to stop both servers" -ForegroundColor Yellow
Write-Host ""
Write-Host "--- Output (Backend + Frontend) ---" -ForegroundColor Gray
Write-Host ""

# Show both Job outputs in real time
try {
    $jobs = @($backendJob, $frontendJob)
    $running = $true
    
    while ($running) {
        $anyRunning = $false
        
        foreach ($job in $jobs) {
            if ($job.State -eq "Running") {
                $anyRunning = $true
                $output = Receive-Job -Job $job -ErrorAction SilentlyContinue
                if ($output) {
                    $prefix = if ($job.Id -eq $backendJob.Id) { "[BACKEND] " } else { "[FRONTEND] " }
                    foreach ($line in $output) {
                        Write-Host "$prefix$line" -ForegroundColor $(if ($job.Id -eq $backendJob.Id) { "Cyan" } else { "Magenta" })
                    }
                }
            }
        }
        
        if (-not $anyRunning) {
            $running = $false
        }
        
        Start-Sleep -Milliseconds 500
    }
} catch {
    Write-Host ""
    Write-Host "Error occurred: $_" -ForegroundColor Red
} finally {
    # Stop all Jobs
    Write-Host ""
    Write-Host "Stopping servers..." -ForegroundColor Red
    Stop-Job -Job $backendJob, $frontendJob -ErrorAction SilentlyContinue
    Remove-Job -Job $backendJob, $frontendJob -Force -ErrorAction SilentlyContinue
    Write-Host "All servers stopped." -ForegroundColor Green
}
