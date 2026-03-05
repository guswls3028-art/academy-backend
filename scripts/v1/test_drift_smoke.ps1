# Smoke check: Describe wrappers and DRIFT consistency. No AWS changes.
# Run from repo root: pwsh scripts/v1/test_drift_smoke.ps1
# Requires: AWS configured, params.yaml, and existing SSOT resources (or run will report missing).
$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
Push-Location $RepoRoot | Out-Null

. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
. (Join-Path $ScriptRoot "core\diff.ps1")

Load-SSOT -Env prod | Out-Null
$script:PlanMode = $true
$R = $script:Region

$driftRows = Get-StructuralDrift
$fail = 0

# 1) Batch CE
foreach ($ceName in $script:SSOT_CE) {
    $r = Invoke-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $ceName, "--region", $R, "--output", "json")
    $ces = if ($r -and $r.PSObject.Properties['computeEnvironments']) { @($r.computeEnvironments) } else { @() }
    $existsInAws = ($ces.Count -gt 0 -and $ces[0])
    $driftRow = $driftRows | Where-Object { $_.ResourceType -eq "Batch CE" -and $_.Name -eq $ceName } | Select-Object -First 1
    $driftSaysMissing = $driftRow -and $driftRow.Actual -eq "missing"
    if ($existsInAws -and $driftSaysMissing) {
        Write-Host "FAIL: Batch CE $ceName exists in AWS but DRIFT says missing" -ForegroundColor Red
        $fail = 1
    } elseif ($existsInAws) {
        Write-Host "PASS: Batch CE $ceName exists, DRIFT Actual=$($driftRow.Actual)" -ForegroundColor Green
    } else {
        Write-Host "SKIP: Batch CE $ceName not in AWS" -ForegroundColor Yellow
    }
}

# 2) Batch Queue: same pattern
foreach ($qName in $script:SSOT_Queue) {
    $r = Invoke-AwsJson @("batch", "describe-job-queues", "--job-queues", $qName, "--region", $R, "--output", "json")
    $queues = if ($r -and $r.PSObject.Properties['jobQueues']) { @($r.jobQueues) } else { @() }
    $existsInAws = ($queues.Count -gt 0 -and $queues[0])
    $driftRow = $driftRows | Where-Object { $_.ResourceType -eq "Batch Queue" -and $_.Name -eq $qName } | Select-Object -First 1
    $driftSaysMissing = $driftRow -and $driftRow.Actual -eq "missing"
    if ($existsInAws -and $driftSaysMissing) {
        Write-Host "FAIL: Batch Queue $qName exists in AWS but DRIFT says missing" -ForegroundColor Red
        $fail = 1
    } elseif ($existsInAws) {
        Write-Host "PASS: Batch Queue $qName exists, DRIFT Actual=$($driftRow.Actual)" -ForegroundColor Green
    } else {
        Write-Host "SKIP: Batch Queue $qName not in AWS" -ForegroundColor Yellow
    }
}

# 3) ASG
foreach ($asgName in $script:SSOT_ASG) {
    $r = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $asgName, "--region", $R, "--output", "json")
    $asgs = if ($r -and $r.PSObject.Properties['AutoScalingGroups']) { @($r.AutoScalingGroups) } else { @() }
    $existsInAws = ($asgs.Count -gt 0 -and $asgs[0])
    $driftRow = $driftRows | Where-Object { $_.ResourceType -eq "ASG" -and $_.Name -eq $asgName } | Select-Object -First 1
    $driftSaysMissing = $driftRow -and $driftRow.Actual -eq "missing"
    if ($existsInAws -and $driftSaysMissing) {
        Write-Host "FAIL: ASG $asgName exists in AWS but DRIFT says missing" -ForegroundColor Red
        $fail = 1
    } elseif ($existsInAws) {
        Write-Host "PASS: ASG $asgName exists, DRIFT Actual=$($driftRow.Actual)" -ForegroundColor Green
    } else {
        Write-Host "SKIP: ASG $asgName not in AWS" -ForegroundColor Yellow
    }
}

# 4) EventBridge rule: describe-rule returns object or fails
foreach ($ruleName in $script:SSOT_EventBridgeRule) {
    try {
        $rule = Invoke-AwsJson @("events", "describe-rule", "--name", $ruleName, "--region", $R, "--output", "json")
        $existsInAws = [bool]$rule
        $driftRow = $driftRows | Where-Object { $_.ResourceType -eq "EventBridge" -and $_.Name -eq $ruleName } | Select-Object -First 1
        $driftSaysMissing = $driftRow -and $driftRow.Actual -eq "missing"
        if ($existsInAws -and $driftSaysMissing) {
            Write-Host "FAIL: EventBridge $ruleName exists but DRIFT says missing" -ForegroundColor Red
            $fail = 1
        } elseif ($existsInAws) {
            Write-Host "PASS: EventBridge $ruleName exists, DRIFT Actual=$($driftRow.Actual)" -ForegroundColor Green
        } else {
            Write-Host "SKIP: EventBridge $ruleName not in AWS" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "SKIP: EventBridge $ruleName describe error" -ForegroundColor Yellow
    }
}

Write-Host "`n=== DRIFT table (from Get-StructuralDrift) ===" -ForegroundColor Cyan
$driftRows | ForEach-Object { Write-Host "  $($_.ResourceType) | $($_.Name) | Actual=$($_.Actual) | Action=$($_.Action)" -ForegroundColor Gray }

if ($fail -eq 0) {
    Write-Host "`nSmoke OK: DRIFT matches Describe results." -ForegroundColor Green
} else {
    Write-Host "`nSmoke FAIL: DRIFT inconsistent with Describe." -ForegroundColor Red
}
Pop-Location
exit $fail
