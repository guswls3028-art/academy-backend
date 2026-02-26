# API: health check via EIP. Optional container recreate not implemented here (use sync_api_env + refresh_api_container_env).
function Confirm-APIHealth {
    Write-Step "API health"
    try {
        $r = Invoke-WebRequest -Uri "$($script:ApiBaseUrl)/health" -UseBasicParsing -TimeoutSec 10
        if ($r.StatusCode -eq 200) { Write-Ok "GET $($script:ApiBaseUrl)/health -> 200" } else { Write-Warn "GET /health -> $($r.StatusCode)" }
    } catch {
        Write-Warn "API health check failed: $_"
    }
}
