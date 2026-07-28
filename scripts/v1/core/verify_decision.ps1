function Test-VerifyDriftRows {
    param(
        [object[]]$Rows,
        [string]$ResourceType,
        [int]$ExpectedCount
    )

    $matched = @($Rows | Where-Object { $_.ResourceType -eq $ResourceType })
    return (
        $matched.Count -eq $ExpectedCount -and
        @($matched | Where-Object { $_.Action -ne "NoOp" }).Count -eq 0
    )
}

function Assert-VerifyClosure {
    param(
        [bool]$NoOp,
        [bool]$BatchCompute,
        [bool]$BatchQueue,
        [bool]$EventBridge,
        [bool]$Asg,
        [bool]$ApiLaunchTemplate,
        [bool]$ApiHealth,
        [bool]$SsmWorkersEnv,
        [bool]$ReportSaved
    )

    $failed = [System.Collections.Generic.List[string]]::new()
    $checks = [ordered]@{
        "idempotent deploy rerun" = $NoOp
        "Batch compute environments" = $BatchCompute
        "Batch queues" = $BatchQueue
        "EventBridge rules" = $EventBridge
        "Auto Scaling groups" = $Asg
        "API launch template" = $ApiLaunchTemplate
        "API health" = $ApiHealth
        "SSM worker environment" = $SsmWorkersEnv
        "verify.latest.md persistence" = $ReportSaved
    }
    foreach ($entry in $checks.GetEnumerator()) {
        if (-not $entry.Value) { $failed.Add($entry.Key) }
    }
    if ($failed.Count -gt 0) {
        throw "Verification closure failed: $($failed -join ', ')"
    }
}
