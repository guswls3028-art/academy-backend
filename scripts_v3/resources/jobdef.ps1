# Ensure Job Definitions with drift detection. Register new revision only when image/vcpus/memory/command/roles/logConfig/timeout differ.
# Uses scripts/infra/batch/*.json. Requires $script:BatchIam, $script:EcrRepoUri (or default repo:latest).
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$InfraPath = Join-Path $RepoRoot "scripts\infra"
$BatchPath = Join-Path $InfraPath "batch"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Get-DesiredJobDefSpec {
    param([string]$TemplatePath)
    $content = [System.IO.File]::ReadAllText($TemplatePath, $utf8NoBom)
    $ecr = $script:EcrRepoUri
    if (-not $ecr) { $ecr = "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$($script:VideoWorkerRepo):latest" }
    $content = $content -replace "PLACEHOLDER_ECR_URI", $ecr
    $content = $content -replace "PLACEHOLDER_JOB_ROLE_ARN", $script:BatchIam.JobRoleArn
    $content = $content -replace "PLACEHOLDER_EXECUTION_ROLE_ARN", $script:BatchIam.ExecutionRoleArn
    $content = $content -replace "PLACEHOLDER_REGION", $script:Region
    return $content | ConvertFrom-Json
}

function Test-JobDefDrift {
    param($Desired, $Current)
    if (-not $Current -or -not $Current.containerProperties) { return $true }
    $c = $Current.containerProperties
    $d = $Desired.containerProperties
    if ($c.image -ne $d.image) { return $true }
    if ([int]$c.vcpus -ne [int]$d.vcpus) { return $true }
    if ([int]$c.memory -ne [int]$d.memory) { return $true }
    $cmdCur = ($c.command | ConvertTo-Json -Compress)
    $cmdDes = ($d.command | ConvertTo-Json -Compress)
    if ($cmdCur -ne $cmdDes) { return $true }
    if ($c.jobRoleArn -ne $d.jobRoleArn) { return $true }
    if ($c.executionRoleArn -ne $d.executionRoleArn) { return $true }
    $logC = $c.logConfiguration.options
    $logD = $d.logConfiguration.options
    if ($logC."awslogs-group" -ne $logD."awslogs-group") { return $true }
    if ($logC."awslogs-stream-prefix" -ne $logD."awslogs-stream-prefix") { return $true }
    $timeCur = if ($Current.timeout) { $Current.timeout.attemptDurationSeconds } else { 0 }
    $timeDes = if ($Desired.timeout) { $Desired.timeout.attemptDurationSeconds } else { 0 }
    if ($timeCur -ne $timeDes) { return $true }
    return $false
}

function Register-JobDefFromJson {
    param([string]$JsonPath, [string]$Name)
    $content = [System.IO.File]::ReadAllText($JsonPath, $utf8NoBom)
    $ecr = $script:EcrRepoUri
    if (-not $ecr) { $ecr = "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$($script:VideoWorkerRepo):latest" }
    $content = $content -replace "PLACEHOLDER_ECR_URI", $ecr
    $content = $content -replace "PLACEHOLDER_JOB_ROLE_ARN", $script:BatchIam.JobRoleArn
    $content = $content -replace "PLACEHOLDER_EXECUTION_ROLE_ARN", $script:BatchIam.ExecutionRoleArn
    $content = $content -replace "PLACEHOLDER_REGION", $script:Region
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        $out = Invoke-Aws @("batch", "register-job-definition", "--cli-input-json", "file://$($tmp -replace '\\','/')", "--region", $script:Region) 2>&1 | Out-String
        $obj = $out | ConvertFrom-Json
        return $obj.jobDefinitionArn
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function Ensure-JobDefOne {
    param([string]$JobDefName, [string]$TemplateFileName)
    Write-Step "Ensure JobDef $JobDefName"
    $templatePath = Join-Path $BatchPath $TemplateFileName
    if (-not (Test-Path $templatePath)) { Write-Warn "Template $templatePath not found."; return $JobDefName }
    $desired = Get-DesiredJobDefSpec -TemplatePath $templatePath
    $list = Invoke-AwsJson @("batch", "describe-job-definitions", "--job-definition-name", $JobDefName, "--status", "ACTIVE", "--region", $script:Region, "--output", "json")
    $latest = $null
    if ($list -and $list.jobDefinitions -and $list.jobDefinitions.Count -gt 0) {
        $latest = $list.jobDefinitions | Sort-Object -Property revision -Descending | Select-Object -First 1
    }
    $drift = Test-JobDefDrift -Desired $desired -Current $latest
    if (-not $latest) {
        Write-Host "  Registering (no ACTIVE revision)" -ForegroundColor Yellow
        $arn = Register-JobDefFromJson -JsonPath $templatePath -Name $JobDefName
        Write-Ok "Registered $arn"
        return $JobDefName
    }
    if ($drift) {
        Write-Host "  Drift detected; registering new revision" -ForegroundColor Yellow
        $arn = Register-JobDefFromJson -JsonPath $templatePath -Name $JobDefName
        Write-Ok "Registered $arn"
        return $JobDefName
    }
    Write-Ok "JobDef $JobDefName revision $($latest.revision) unchanged"
    return $JobDefName
}

function Ensure-VideoJobDef {
    Ensure-JobDefOne -JobDefName $script:VideoJobDefName -TemplateFileName "video_job_definition.json" | Out-Null
}

function Ensure-OpsJobDefReconcile {
    Ensure-JobDefOne -JobDefName $script:OpsJobDefReconcile -TemplateFileName "video_ops_job_definition_reconcile.json" | Out-Null
}

function Ensure-OpsJobDefScanStuck {
    Ensure-JobDefOne -JobDefName $script:OpsJobDefScanStuck -TemplateFileName "video_ops_job_definition_scanstuck.json" | Out-Null
}

function Ensure-OpsJobDefNetprobe {
    Ensure-JobDefOne -JobDefName $script:OpsJobDefNetprobe -TemplateFileName "video_ops_job_definition_netprobe.json" | Out-Null
}
