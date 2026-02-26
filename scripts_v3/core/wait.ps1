# SSOT v3 — Wait 루프 (CE 삭제/VALID, Queue DISABLED 등)
function Wait-ForComputeEnvironmentDeleted {
    param(
        [string]$CeName,
        [string]$Region,
        [int]$TimeoutSeconds = 300
    )
    $elapsed = 0
    while ($elapsed -lt $TimeoutSeconds) {
        $out = aws batch describe-compute-environments --compute-environments $CeName --region $Region --output json 2>&1 | ConvertFrom-Json
        if (-not $out.computeEnvironments -or $out.computeEnvironments.Count -eq 0) {
            return $true
        }
        Start-Sleep -Seconds 10
        $elapsed += 10
        Write-Host "  Waiting for CE $CeName deleted... ${elapsed}s" -ForegroundColor Gray
    }
    throw "Timeout waiting for CE $CeName to be deleted"
}

function Wait-ForComputeEnvironmentValid {
    param(
        [string]$CeName,
        [string]$Region,
        [int]$TimeoutSeconds = 600
    )
    $elapsed = 0
    while ($elapsed -lt $TimeoutSeconds) {
        $out = aws batch describe-compute-environments --compute-environments $CeName --region $Region --output json 2>&1 | ConvertFrom-Json
        $ce = $out.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $CeName } | Select-Object -First 1
        if ($ce.status -eq "VALID") { return $ce }
        if ($ce.status -eq "INVALID") { throw "CE $CeName is INVALID" }
        Start-Sleep -Seconds 10
        $elapsed += 10
        Write-Host "  Waiting for CE $CeName VALID... status=$($ce.status) ${elapsed}s" -ForegroundColor Gray
    }
    throw "Timeout waiting for CE $CeName VALID"
}

function Wait-ForJobQueueState {
    param(
        [string]$QueueName,
        [string]$Region,
        [string]$ExpectedState = "DISABLED",
        [int]$TimeoutSeconds = 90
    )
    $elapsed = 0
    while ($elapsed -lt $TimeoutSeconds) {
        $out = aws batch describe-job-queues --job-queues $QueueName --region $Region --output json 2>&1 | ConvertFrom-Json
        $q = $out.jobQueues | Where-Object { $_.jobQueueName -eq $QueueName } | Select-Object -First 1
        if ($q.state -eq $ExpectedState) { return $q }
        Start-Sleep -Seconds 5
        $elapsed += 5
    }
    throw "Timeout waiting for queue $QueueName state=$ExpectedState"
}
