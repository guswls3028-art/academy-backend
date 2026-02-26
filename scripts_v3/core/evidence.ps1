# SSOT v3 — Evidence 표 출력 (state-contract 준수)
function Get-AwsJson {
    param([string[]]$ArgsArray)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @ArgsArray 2>&1
    $ErrorActionPreference = $prev
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

function Show-Evidence {
    param([string]$Region = $script:Region)

    Write-Step "Evidence" "Yellow"

    $r = $Region
    $rows = @()

    # Video CE
    $ceV = Get-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:VideoCEName, "--region", $r, "--output", "json")
    $ceVo = $ceV.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $script:VideoCEName } | Select-Object -First 1
    if ($ceVo) {
        $rows += [PSCustomObject]@{ Resource = "Video CE"; Name = $script:VideoCEName; Status = $ceVo.status; State = $ceVo.state; Arn = $ceVo.computeEnvironmentArn }
    } else {
        $rows += [PSCustomObject]@{ Resource = "Video CE"; Name = $script:VideoCEName; Status = "MISSING"; State = ""; Arn = "" }
    }

    # Video Queue
    $jqV = Get-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:VideoQueueName, "--region", $r, "--output", "json")
    $jqVo = $jqV.jobQueues | Where-Object { $_.jobQueueName -eq $script:VideoQueueName } | Select-Object -First 1
    if ($jqVo) {
        $rows += [PSCustomObject]@{ Resource = "Video Queue"; Name = $script:VideoQueueName; Status = $jqVo.status; State = $jqVo.state; Arn = $jqVo.jobQueueArn }
    } else {
        $rows += [PSCustomObject]@{ Resource = "Video Queue"; Name = $script:VideoQueueName; Status = "MISSING"; State = ""; Arn = "" }
    }

    # Ops CE
    $ceO = Get-AwsJson @("batch", "describe-compute-environments", "--compute-environments", $script:OpsCEName, "--region", $r, "--output", "json")
    $ceOo = $ceO.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq $script:OpsCEName } | Select-Object -First 1
    if ($ceOo) {
        $rows += [PSCustomObject]@{ Resource = "Ops CE"; Name = $script:OpsCEName; Status = $ceOo.status; State = $ceOo.state; Arn = $ceOo.computeEnvironmentArn }
    } else {
        $rows += [PSCustomObject]@{ Resource = "Ops CE"; Name = $script:OpsCEName; Status = "MISSING"; State = ""; Arn = "" }
    }

    # Ops Queue
    $jqO = Get-AwsJson @("batch", "describe-job-queues", "--job-queues", $script:OpsQueueName, "--region", $r, "--output", "json")
    $jqOo = $jqO.jobQueues | Where-Object { $_.jobQueueName -eq $script:OpsQueueName } | Select-Object -First 1
    if ($jqOo) {
        $rows += [PSCustomObject]@{ Resource = "Ops Queue"; Name = $script:OpsQueueName; Status = $jqOo.status; State = $jqOo.state; Arn = $jqOo.jobQueueArn }
    } else {
        $rows += [PSCustomObject]@{ Resource = "Ops Queue"; Name = $script:OpsQueueName; Status = "MISSING"; State = ""; Arn = "" }
    }

    # JobDef revision
    $jd = Get-AwsJson @("batch", "describe-job-definitions", "--job-definition-name", $script:VideoJobDefName, "--status", "ACTIVE", "--region", $r, "--output", "json")
    $jdLatest = $jd.jobDefinitions | Sort-Object -Property revision -Descending | Select-Object -First 1
    if ($jdLatest) {
        $img = $jdLatest.containerProperties.image
        $digestLine = ""
        if ($img -match '\.amazonaws\.com/([^:]+):(.+)$') {
            $digestOut = & aws ecr describe-images --repository-name $Matches[1] --image-ids "imageTag=$($Matches[2])" --query "imageDetails[0].imageDigest" --output text --region $r 2>&1
            if ($LASTEXITCODE -eq 0 -and $digestOut) { $digestLine = " digest=$($digestOut.Trim())" }
        }
        $rows += [PSCustomObject]@{ Resource = "Video JobDef"; Name = $script:VideoJobDefName; Status = "rev $($jdLatest.revision)"; State = $img; Arn = $jdLatest.jobDefinitionArn + $digestLine }
    }

    # EventBridge
    $ruleR = Get-AwsJson @("events", "describe-rule", "--name", $script:ReconcileRuleName, "--region", $r, "--output", "json")
    if ($ruleR) {
        $rows += [PSCustomObject]@{ Resource = "EventBridge Reconcile"; Name = $script:ReconcileRuleName; Status = $ruleR.State; State = $ruleR.ScheduleExpression; Arn = $ruleR.Arn }
    }
    $ruleS = Get-AwsJson @("events", "describe-rule", "--name", $script:ScanStuckRuleName, "--region", $r, "--output", "json")
    if ($ruleS) {
        $rows += [PSCustomObject]@{ Resource = "EventBridge ScanStuck"; Name = $script:ScanStuckRuleName; Status = $ruleS.State; State = $ruleS.ScheduleExpression; Arn = $ruleS.Arn }
    }

    $rows | Format-Table -AutoSize
    Write-Host "`n=== EVIDENCE END ===" -ForegroundColor Yellow
}
