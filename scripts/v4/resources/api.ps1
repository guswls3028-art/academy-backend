# API: EIP instance + /health. Validate only.
function Get-APIInstanceByEIP {
    $addr = Invoke-AwsJson @("ec2", "describe-addresses", "--allocation-ids", $script:ApiAllocationId, "--region", $script:Region, "--output", "json")
    if (-not $addr -or -not $addr.Addresses -or $addr.Addresses.Count -eq 0 -or -not $addr.Addresses[0].InstanceId) {
        return $null
    }
    return $addr.Addresses[0].InstanceId
}

function Confirm-APIHealth {
    Write-Step "API health"
    if ($script:PlanMode) { Write-Ok "API check skipped (Plan)"; return }
    try {
        $r = Invoke-WebRequest -Uri "$($script:ApiBaseUrl)/health" -UseBasicParsing -TimeoutSec 10
        if ($r.StatusCode -eq 200) { Write-Ok "GET $($script:ApiBaseUrl)/health -> 200" } else { Write-Warn "GET /health -> $($r.StatusCode)" }
    } catch {
        Write-Warn "API health check failed: $_"
    }
}
