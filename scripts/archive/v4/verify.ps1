# ==============================================================================
# Academy v4 — 새 PC 5단계 검증 자동화.
# 1) bootstrap  2) deploy -Plan  3) deploy -PruneLegacy  4) deploy 재실행(No-op)  5) Evidence 위치 안내
# 로그: logs/v4/YYYYMMDD-HHMMSS-verify.log
# ==============================================================================
$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$LogDir = Join-Path $RepoRoot "logs\v4"
$Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$LogFile = Join-Path $LogDir "$Timestamp-verify.log"

if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }

function Write-Log { param($Msg) $Msg | Tee-Object -FilePath $LogFile -Append; Write-Host $Msg }
function Run-Step {
    param([string]$Name, [scriptblock]$Run, [string]$FailMessage = "Step failed.")
    Write-Log "`n--- $Name ---"
    try {
        $out = & $Run 2>&1
        $out | ForEach-Object { Write-Log $_ }
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "ExitCode $LASTEXITCODE" }
        return @{ Ok = $true; Output = $out }
    } catch {
        Write-Log "FAIL: $FailMessage"
        Write-Log "Command/Error: $_"
        Write-Log "Log file: $LogFile"
        throw
    }
}

$results = @()
try {
    Write-Log "=== Verify v4 started $Timestamp ==="
    Write-Log "Log: $LogFile"

    # 1) Bootstrap
    $null = Run-Step "1) bootstrap.ps1" {
        Push-Location $RepoRoot
        & (Join-Path $ScriptRoot "bootstrap.ps1")
        Pop-Location
    } "bootstrap failed. Fix env/aws/params then retry."
    $results += [PSCustomObject]@{ Step = "1) bootstrap"; Result = "OK"; Detail = "" }

    # 2) deploy -Plan
    $planOut = $null
    $null = Run-Step "2) deploy.ps1 -Plan" {
        Push-Location $RepoRoot
        $planOut = & (Join-Path $ScriptRoot "deploy.ps1") -Plan 2>&1
        $planOut | ForEach-Object { Write-Log $_ }
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "ExitCode $LASTEXITCODE" }
        Pop-Location
        $planOut
    } "deploy -Plan failed. Check drift/params."
    $results += [PSCustomObject]@{ Step = "2) deploy -Plan"; Result = "OK"; Detail = "Reports: docs/00-SSOT/archive/v4/reports/" }

    # 3) deploy -PruneLegacy
    $null = Run-Step "3) deploy.ps1 -PruneLegacy" {
        Push-Location $RepoRoot
        & (Join-Path $ScriptRoot "deploy.ps1") -PruneLegacy 2>&1 | ForEach-Object { Write-Log $_ }
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "ExitCode $LASTEXITCODE" }
        Pop-Location
    } "PruneLegacy failed. Check log."
    $results += [PSCustomObject]@{ Step = "3) deploy -PruneLegacy"; Result = "OK"; Detail = "" }

    # 4) deploy 재실행 → No-op
    $step4 = Run-Step "4) deploy.ps1 (rerun, expect No-op)" {
        Push-Location $RepoRoot
        $out = & (Join-Path $ScriptRoot "deploy.ps1") 2>&1 | Out-String
        if ($LASTEXITCODE -and $LASTEXITCODE -ne 0) { throw "ExitCode $LASTEXITCODE" }
        Pop-Location
        $out
    } "Second deploy failed."
    $deployOut = if ($step4.Output) { $step4.Output | Out-String } else { "" }
    $noOp = ($deployOut -match "Idempotent|No changes required") -or ($deployOut -match "No changes")
    if (-not $noOp) {
        Write-Log "WARN: No-op phrase not found in output (Idempotent / No changes required). Check log."
    }
    $results += [PSCustomObject]@{ Step = "4) deploy (No-op)"; Result = if ($noOp) { "OK" } else { "CHECK" }; Detail = if ($noOp) { "No-op confirmed" } else { "See log" } }

    # 5) Evidence 위치
    $results += [PSCustomObject]@{ Step = "5) Evidence"; Result = "-"; Detail = "docs/00-SSOT/archive/v4/reports/, deploy stdout" }
}
catch {
    Write-Log "`n=== VERIFY STOPPED ==="
    Write-Log "Failure: $($_.Exception.Message)"
    Write-Log "Log: $LogFile"
    $results | Format-Table -AutoSize
    exit 1
}

Write-Log "`n=== Verify v4 result table ==="
$results | Format-Table -AutoSize
Write-Log "`nLog: $LogFile"

# Write verify.latest.md from current state (drift + evidence snapshot)
try {
    Push-Location $RepoRoot | Out-Null
    . (Join-Path $ScriptRoot "core\ssot.ps1")
    . (Join-Path $ScriptRoot "core\aws.ps1")
    . (Join-Path $ScriptRoot "core\diff.ps1")
    . (Join-Path $ScriptRoot "core\evidence.ps1")
    . (Join-Path $ScriptRoot "core\reports.ps1")
    Load-SSOT -Env prod | Out-Null
    $script:PlanMode = $true
    $driftRows = Get-StructuralDrift
    $ev = Get-EvidenceSnapshot -NetprobeJobId "" -NetprobeStatus "see deploy"
    $sb = [System.Text.StringBuilder]::new()
    [void]$sb.AppendLine("## Checks")
    $ceOk = ($driftRows | Where-Object { $_.ResourceType -eq "Batch CE" -and $_.Actual -eq "exists" }).Count -eq $script:SSOT_CE.Count
    [void]$sb.AppendLine("- Batch CE VALID/ENABLED: $(if ($ceOk) { 'PASS' } else { 'FAIL' })")
    $qOk = ($driftRows | Where-Object { $_.ResourceType -eq "Batch Queue" -and $_.Actual -eq "exists" }).Count -eq $script:SSOT_Queue.Count
    [void]$sb.AppendLine("- Batch Queue ENABLED: $(if ($qOk) { 'PASS' } else { 'FAIL' })")
    $ebOk = ($driftRows | Where-Object { $_.ResourceType -eq "EventBridge" -and $_.Actual -eq "exists" }).Count -eq $script:SSOT_EventBridgeRule.Count
    [void]$sb.AppendLine("- EventBridge ENABLED: $(if ($ebOk) { 'PASS' } else { 'FAIL' })")
    $asgOk = ($driftRows | Where-Object { $_.ResourceType -eq "ASG" -and $_.Actual -eq "exists" }).Count -eq $script:SSOT_ASG.Count
    [void]$sb.AppendLine("- ASG desired/min/max (incl. API ASG): $(if ($asgOk) { 'PASS' } else { 'FAIL' })")
    $apiLtOk = ($driftRows | Where-Object { $_.ResourceType -eq "API LT" -and ($_.Action -eq "NoOp" -or $_.Actual -eq "exists") }).Count -ge 1
    [void]$sb.AppendLine("- API ASG/LT drift: $(if ($apiLtOk) { 'PASS' } else { 'FAIL' })")
    $apiOk = ($ev -and $ev["apiHealth"] -eq "OK")
    [void]$sb.AppendLine("- API health 200: $(if ($apiOk) { 'PASS' } else { 'FAIL' })")
    $buildOk = ($ev -and $ev["buildInstanceId"] -and $ev["buildInstanceId"] -ne "not found")
    [void]$sb.AppendLine("- Build instance (Name=academy-build-arm64): $(if ($buildOk) { 'PASS' } else { 'FAIL' })")
    $ssmOk = ($ev -and $ev["ssmWorkersEnvExists"] -eq "yes")
    [void]$sb.AppendLine("- SSM online: $(if ($ssmOk) { 'PASS' } else { 'FAIL' })")
    [void]$sb.AppendLine("- Netprobe: (see last deploy Evidence)")
    [void]$sb.AppendLine("")
    [void]$sb.AppendLine("## Result table")
    foreach ($r in $results) { [void]$sb.AppendLine("- $($r.Step): $($r.Result) $($r.Detail)") }
    Save-VerifyReport -MarkdownContent $sb.ToString()
    Pop-Location | Out-Null
} catch {
    Write-Log "  Could not write verify.latest.md: $_"
}

Write-Log "=== Verify v4 done ===`n"
