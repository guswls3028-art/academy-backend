# Wait until CE is deleted (not in describe list). Timeout 300s, poll 10s.
function Wait-CEDeleted {
    param([string]$CEName, [string]$Reg, [int]$TimeoutSec = 300)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $r = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $CEName, "--region", $Reg, "--output", "json")
        if (-not $r -or -not $r.computeEnvironments -or $r.computeEnvironments.Count -eq 0) {
            Write-Ok "CE $CEName deleted"
            return
        }
        Write-Host "  Waiting for CE $CEName to be deleted..." -ForegroundColor Gray
        Start-Sleep -Seconds 10
        $elapsed += 10
    }
    throw "Timeout waiting for CE $CEName to be deleted (${TimeoutSec}s)"
}

# Wait until CE status=VALID and state=ENABLED. Timeout 600s.
function Wait-CEValidEnabled {
    param([string]$CEName, [string]$Reg, [int]$TimeoutSec = 600)
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $r = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $CEName, "--region", $Reg, "--output", "json")
        if (-not $r -or -not $r.computeEnvironments -or $r.computeEnvironments.Count -eq 0) {
            Start-Sleep -Seconds 15
            $elapsed += 15
            continue
        }
        $ce = $r.computeEnvironments[0]
        $status = $ce.status
        $state = $ce.state
        Write-Host "  CE $CEName status=$status state=$state" -ForegroundColor Gray
        if ($status -eq "VALID" -and $state -eq "ENABLED") {
            Write-Ok "CE $CEName VALID and ENABLED"
            return
        }
        if ($status -eq "INVALID") {
            throw "CE $CEName is INVALID. statusReason: $($ce.statusReason)"
        }
        Start-Sleep -Seconds 15
        $elapsed += 15
    }
    throw "Timeout waiting for CE $CEName VALID/ENABLED (${TimeoutSec}s)"
}
