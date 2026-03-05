# JobDef: drift-based register. Uses v1/templates/batch.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
$ErrorActionPreference = "Stop"
$V4Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BatchPath = Join-Path $V4Root "templates\batch"
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

function Get-DesiredJobDefSpec { param([string]$TemplatePath)
    $content = [System.IO.File]::ReadAllText($TemplatePath, $utf8NoBom)
    $ecr = $script:EcrRepoUri
    if (-not $ecr) {
        if ($script:EcrImmutableTagRequired) { throw "EcrRepoUri required (ecr.immutableTagRequired is true). Pass -EcrRepoUri <image:tag>." }
        $ecr = "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$($script:VideoWorkerRepo):latest"
    }
    if ($ecr -match ':latest\s*$') {
        throw ":latest tag is prohibited for JobDef. Pass -EcrRepoUri with an immutable tag (e.g. commit SHA)."
    }
    $content = $content -replace "PLACEHOLDER_ECR_URI", $ecr
    $content = $content -replace "PLACEHOLDER_JOB_ROLE_ARN", $script:BatchIam.JobRoleArn
    $content = $content -replace "PLACEHOLDER_EXECUTION_ROLE_ARN", $script:BatchIam.ExecutionRoleArn
    $content = $content -replace "PLACEHOLDER_REGION", $script:Region
    return $content | ConvertFrom-Json
}

function Test-JobDefDrift { param($Desired, $Current)
    if (-not $Current -or -not $Current.containerProperties) { return $true }
    $c = $Current.containerProperties
    $d = $Desired.containerProperties
    if ($c.image -ne $d.image) { return $true }
    if ([int]$c.vcpus -ne [int]$d.vcpus) { return $true }
    if ([int]$c.memory -ne [int]$d.memory) { return $true }
    return $false
}

function Register-JobDefFromJson { param([string]$JsonPath, [string]$Name)
    $content = [System.IO.File]::ReadAllText($JsonPath, $utf8NoBom)
    $ecr = $script:EcrRepoUri
    if (-not $ecr) {
        if ($script:EcrImmutableTagRequired) { throw "EcrRepoUri required (ecr.immutableTagRequired is true). Pass -EcrRepoUri <image:tag>." }
        $ecr = "$($script:AccountId).dkr.ecr.$($script:Region).amazonaws.com/$($script:VideoWorkerRepo):latest"
    }
    if ($ecr -match ':latest\s*$') {
        throw ":latest tag is prohibited for JobDef. Pass -EcrRepoUri with an immutable tag (e.g. commit SHA)."
    }
    $content = $content -replace "PLACEHOLDER_ECR_URI", $ecr
    $content = $content -replace "PLACEHOLDER_JOB_ROLE_ARN", $script:BatchIam.JobRoleArn
    $content = $content -replace "PLACEHOLDER_EXECUTION_ROLE_ARN", $script:BatchIam.ExecutionRoleArn
    $content = $content -replace "PLACEHOLDER_REGION", $script:Region
    $tmp = [System.IO.Path]::GetTempFileName()
    [System.IO.File]::WriteAllText($tmp, $content, $utf8NoBom)
    try {
        $fileArg = "file://$($tmp -replace '\\','/')"
        $raw = Invoke-Aws @("batch", "register-job-definition", "--cli-input-json", $fileArg, "--region", $script:Region, "--output", "json") -ErrorMessage "register-job-definition"
        $obj = ($raw | Out-String).Trim() | ConvertFrom-Json
        return $obj.jobDefinitionArn
    } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
}

function Ensure-JobDefOne { param([string]$JobDefName, [string]$TemplateFileName)
    if ($script:PlanMode) { return }
    Write-Step "Ensure JobDef $JobDefName"
    $templatePath = Join-Path $BatchPath $TemplateFileName
    if (-not (Test-Path $templatePath)) { Write-Warn "Template $templatePath not found."; return }
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
        $script:ChangesMade = $true
        Write-Ok "Registered $arn"
        return
    }
    if ($drift) {
        Write-Host "  Drift detected; registering new revision" -ForegroundColor Yellow
        $arn = Register-JobDefFromJson -JsonPath $templatePath -Name $JobDefName
        $script:ChangesMade = $true
        Write-Ok "Registered $arn"
        return
    }
    Write-Ok "JobDef $JobDefName revision $($latest.revision) unchanged"
}

function Ensure-VideoJobDef { Ensure-JobDefOne -JobDefName $script:VideoJobDefName -TemplateFileName "video_job_definition.json" | Out-Null }
function Ensure-VideoLongJobDef { if ($script:VideoLongJobDefName) { Ensure-JobDefOne -JobDefName $script:VideoLongJobDefName -TemplateFileName "video_job_definition_long.json" | Out-Null } }
function Ensure-OpsJobDefReconcile { Ensure-JobDefOne -JobDefName $script:OpsJobDefReconcile -TemplateFileName "video_ops_job_definition_reconcile.json" | Out-Null }
function Ensure-OpsJobDefScanStuck { Ensure-JobDefOne -JobDefName $script:OpsJobDefScanStuck -TemplateFileName "video_ops_job_definition_scanstuck.json" | Out-Null }
function Ensure-OpsJobDefNetprobe { Ensure-JobDefOne -JobDefName $script:OpsJobDefNetprobe -TemplateFileName "video_ops_job_definition_netprobe.json" | Out-Null }
