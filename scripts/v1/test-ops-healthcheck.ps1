$ErrorActionPreference = "Stop"

# Exercise the normal entry point with synthetic AWS/GitHub responses only.
function Invoke-WebRequest { @{ StatusCode = 200 } }

function gh {
    $global:LASTEXITCODE = 0
    $release = @{
        databaseId = 101; name = "Release"; headBranch = "main"; headSha = "123456789abc"
        createdAt = "2026-11-20T10:30:00Z"; status = "completed"; conclusion = "success"
    }
    if ($args -contains "--workflow") {
        if (($args -join " ") -notmatch "--workflow v1-build-and-push-latest.yml --branch main") {
            throw "Unexpected release query"
        }
        if ($scenario -eq "unavailable") { $global:LASTEXITCODE = 1; return "[]" }
        if ($scenario -eq "missing") { return "[]" }
        if ($scenario -eq "waiting") { $release.status = "waiting"; $release.conclusion = "" }
        return ConvertTo-Json -AsArray $release -Compress
    }
    $cron = $release.Clone()
    $cron.databaseId = 102
    $cron.name = "Dev Alerts Cron"
    $cron.conclusion = "failure"
    ConvertTo-Json -InputObject @($cron, $release) -Compress
}

function aws {
    $global:LASTEXITCODE = 0
    if ($args[0] -ne "cloudwatch" -or $args[1] -ne "describe-alarms") { return "{}" }
    if ($args -notcontains "MetricAlarm" -or $args -notcontains "CompositeAlarm") { throw "Incomplete alarm query" }
    if ($scenario -eq "unavailable") { $global:LASTEXITCODE = 1; return "{}" }
    $composites = @(@{ AlarmName = "academy-api-UserImpact"; StateValue = $alarmState })
    if ($scenario -eq "missing") { $composites = @() }
    $control = @{ AlarmName = "autoscaling"; ActionsEnabled = $true; AlarmActions = @("scalingPolicy"); StateValue = "ALARM" }
    ConvertTo-Json -Depth 4 -Compress -InputObject @{ MetricAlarms = @($control); CompositeAlarms = $composites }
}

function Read-HealthOutput($scenario, $alarmState) {
    (& "$PSScriptRoot/run-ops-healthcheck.ps1" *>&1) -join "`n"
}

function Assert-Contains($output, $expected) {
    if (-not $output.Contains($expected)) { throw "Missing healthcheck output: $expected" }
}

$output = Read-HealthOutput "normal" "OK"
Assert-Contains $output "Release #101 (main@12345678)"
Assert-Contains $output "Dev Alerts Cron #102"
Assert-Contains $output "academy-api-UserImpact: OK"
if ([regex]::Matches($output, "Release #101").Count -ne 1) { throw "Release printed multiple times" }

$output = Read-HealthOutput "waiting" "ALARM"
Assert-Contains $output "UTC — waiting"
Assert-Contains $output "CloudWatch 알람 발생: academy-api-UserImpact"
if ($output.Contains("CloudWatch 알람 발생: autoscaling")) { throw "Scaling control raised an incident" }

$output = Read-HealthOutput "no-data" "INSUFFICIENT_DATA"
Assert-Contains $output "CloudWatch 알람 데이터 부족: academy-api-UserImpact"

$output = Read-HealthOutput "missing" ""
Assert-Contains $output "최근 운영 배포 실행 내역 없음"
Assert-Contains $output "academy-api-UserImpact 경보를 확인할 수 없음"

$output = Read-HealthOutput "unavailable" ""
Assert-Contains $output "최근 운영 배포 조회 실패"
Assert-Contains $output "CloudWatch 알람 조회 실패"
Assert-Contains $output "Dev Alerts Cron #102"
Write-Output "PASS: deployment and CloudWatch healthcheck"
