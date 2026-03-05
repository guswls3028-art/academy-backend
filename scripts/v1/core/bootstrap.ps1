# Bootstrap: One-take 자동 준비. SSM password, SQS, RDS engineVersion, ECR resolve.
# deploy.ps1에서 Preflight 직후·Ensure 직전에 호출. params.yaml은 수정하지 않음.
$ErrorActionPreference = "Stop"

function New-SecureRdsPassword {
    $len = Get-Random -Minimum 20 -Maximum 33
    $chars = [char[]]"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*()-_=+[]{}|;:,.<>?"
    $bad = [char[]]" `"'\/"
    $arr = 1..$len | ForEach-Object {
        $c = $chars[(Get-Random -Maximum $chars.Length)]
        while ($bad -contains $c) { $c = $chars[(Get-Random -Maximum $chars.Length)] }
        $c
    }
    return -join $arr
}

function Invoke-BootstrapSsmRdsPassword {
    if (-not $script:RdsMasterPasswordSsmParam -or $script:RdsMasterPasswordSsmParam.Trim() -eq "") { return }
    if ($script:SkipRds) { return }
    if ($script:PlanMode) { Write-Ok "Bootstrap SSM RDS password skipped (Plan)"; return }

    $existing = $null
    try {
        $existing = Invoke-AwsJson @("ssm", "get-parameter", "--name", $script:RdsMasterPasswordSsmParam, "--with-decryption", "--region", $script:Region, "--output", "json")
    } catch { }
    if ($existing -and $existing.Parameter -and $existing.Parameter.Value) {
        Write-Ok "SSM RDS password already set: $($script:RdsMasterPasswordSsmParam)"
        return
    }
    $password = New-SecureRdsPassword
    Invoke-Aws @("ssm", "put-parameter", "--name", $script:RdsMasterPasswordSsmParam, "--type", "SecureString", "--value", $password, "--overwrite", "--region", $script:Region) -ErrorMessage "put-parameter RDS password" | Out-Null
    Write-Ok "SSM RDS password created: $($script:RdsMasterPasswordSsmParam) (value not logged)"
    $script:ChangesMade = $true
}

function Invoke-BootstrapSqs {
    if ($script:SkipSqs) { return }
    if ($script:PlanMode) { Write-Ok "Bootstrap SQS skipped (Plan)"; return }

    $defaultMsg = "academy-v1-messaging-queue"
    $defaultAi = "academy-v1-ai-queue"
    $msgName = if ($script:MessagingSqsQueueName -and $script:MessagingSqsQueueName.Trim() -ne "") { $script:MessagingSqsQueueName.Trim() } else { $defaultMsg }
    $aiName = if ($script:AiSqsQueueName -and $script:AiSqsQueueName.Trim() -ne "") { $script:AiSqsQueueName.Trim() } else { $defaultAi }

    foreach ($qName in @($msgName, $aiName)) {
        $url = $null
        $get = Invoke-AwsJson @("sqs", "get-queue-url", "--queue-name", $qName, "--region", $script:Region, "--output", "json")
        if ($get -and $get.QueueUrl) { $url = $get.QueueUrl }
        if (-not $url) {
            Invoke-AwsJson @("sqs", "create-queue", "--queue-name", $qName, "--region", $script:Region, "--output", "json") | Out-Null
            $get = Invoke-AwsJson @("sqs", "get-queue-url", "--queue-name", $qName, "--region", $script:Region, "--output", "json")
            if ($get -and $get.QueueUrl) { $url = $get.QueueUrl; $script:ChangesMade = $true }
        }
        if ($url) {
            if ($qName -eq $msgName) { $script:MessagingSqsQueueName = $qName; $script:MessagingSqsQueueUrl = $url; Write-Ok "SQS Messaging: $qName -> $url" }
            else { $script:AiSqsQueueName = $qName; $script:AiSqsQueueUrl = $url; Write-Ok "SQS AI: $qName -> $url" }
        }
    }
}

function Get-RdsEngineVersionResolved {
    param([string]$InputVersion, [string]$Engine = "postgres")
    if ($InputVersion -and $InputVersion.Trim() -ne "" -and $InputVersion -match '^\d+\.\d+\.\d+') { return $InputVersion.Trim() }
    $major = "15"
    if ($InputVersion -and $InputVersion.Trim() -ne "" -and $InputVersion -match '^(\d+)') { $major = $matches[1] }
    $r = Invoke-AwsJson @("rds", "describe-db-engine-versions", "--engine", $Engine, "--region", $script:Region, "--output", "json")
    if (-not $r -or -not $r.DBEngineVersions) { return $null }
    $versions = @($r.DBEngineVersions | ForEach-Object { $_.EngineVersion } | Where-Object { $_ -match "^$major\.\d+" })
    if (-not $versions) { return $null }
    $sorted = $versions | Sort-Object { [version]($_ -replace '-.*$','') } -Descending
    return $sorted[0]
}

function Invoke-BootstrapRdsEngineVersion {
    if (-not $script:RdsDbIdentifier -or $script:RdsDbIdentifier.Trim() -eq "") { return }
    if ($script:SkipRds) { return }
    if ($script:PlanMode) { Write-Ok "Bootstrap RDS engineVersion skipped (Plan)"; return }

    $inputVer = if ($script:RdsEngineVersion) { $script:RdsEngineVersion.Trim() } else { "" }
    $resolved = Get-RdsEngineVersionResolved -InputVersion $inputVer -Engine $script:RdsEngine
    if ($resolved) {
        $script:RdsEngineVersionResolved = $resolved
        if ($inputVer -ne $resolved) { Write-Ok "RDS engineVersion resolved: input='$inputVer' -> resolved=$resolved" }
    } else {
        if ($inputVer) { $script:RdsEngineVersionResolved = $inputVer }
    }
}

function Get-EcrResolvedTag {
    if ($env:GITHUB_SHA) { return $env:GITHUB_SHA.Substring(0, [Math]::Min(12, $env:GITHUB_SHA.Length)) }
    $git = Get-Command git -ErrorAction SilentlyContinue
    if ($git) {
        try {
            $rev = & git rev-parse HEAD 2>$null
            if ($rev -and $rev.Length -ge 7) { return $rev.Substring(0, 7) }
        } catch { }
    }
    $repo = $script:VideoWorkerRepo
    $list = Invoke-AwsJson @("ecr", "describe-images", "--repository-name", $repo, "--region", $script:Region, "--output", "json")
    if (-not $list -or -not $list.imageDetails) { return $null }
    $nonLatest = @($list.imageDetails | Where-Object { $_.imageTags -and ($_.imageTags | Where-Object { $_ -ne "latest" }) } | ForEach-Object {
        $tag = ($_.imageTags | Where-Object { $_ -ne "latest" } | Select-Object -First 1)
        if ($tag) { [PSCustomObject]@{ Tag = $tag; Pushed = $_.imagePushedAt } }
    } | Where-Object { $_ })
    if (-not $nonLatest) { return $null }
    $latest = $nonLatest | Sort-Object { $_.Pushed } -Descending | Select-Object -First 1
    return $latest.Tag
}

function Invoke-BuildServerBuild {
    param([string]$Tag, [string]$Uri)
    if ($script:PlanMode) { return }
    $inst = Get-BuildInstanceByTag
    if (-not $inst) {
        Ensure-Build
        $inst = Get-BuildInstanceByTag
    }
    if (-not $inst) { throw "Bootstrap: Build instance not available. Create build instance or pass -EcrRepoUri." }
    $instanceId = $inst.InstanceId
    $state = $inst.State.Name
    if ($state -eq "stopped") {
        Invoke-Aws @("ec2", "start-instances", "--instance-ids", $instanceId, "--region", $script:Region) -ErrorMessage "start build instance" | Out-Null
        $script:ChangesMade = $true
        $elapsed = 0
        while ($elapsed -lt 300) {
            $d = Invoke-AwsJson @("ec2", "describe-instance-status", "--instance-ids", $instanceId, "--region", $script:Region, "--output", "json")
            if ($d -and $d.InstanceStatuses -and $d.InstanceStatuses.Count -gt 0 -and $d.InstanceStatuses[0].InstanceState.Name -eq "running") { break }
            Start-Sleep -Seconds 15; $elapsed += 15
        }
        Start-Sleep -Seconds 30
    }
    $region = $script:Region
    $repo = $script:VideoWorkerRepo
    $acc = $script:AccountId
    $buildPath = $script:BuildRepoPath
    if (-not $buildPath) { $buildPath = "/opt/academy" }
    $commands = @(
        "set -e",
        "export AWS_DEFAULT_REGION=$region",
        "export TAG=$Tag",
        "cd $buildPath 2>/dev/null || cd \$HOME/academy 2>/dev/null || { echo 'Build repo path not found'; exit 1 }",
        "git fetch origin 2>/dev/null || true",
        "git checkout \$TAG 2>/dev/null || git pull 2>/dev/null || true",
        "aws ecr get-login-password --region $region | docker login --username AWS --password-stdin ${acc}.dkr.ecr.${region}.amazonaws.com",
        "docker build --platform linux/arm64 -t ${repo}:${Tag} -f Dockerfile.video . 2>&1 || docker build --platform linux/arm64 -t ${repo}:${Tag} . 2>&1",
        "docker tag ${repo}:${Tag} ${acc}.dkr.ecr.${region}.amazonaws.com/${repo}:${Tag}",
        "docker push ${acc}.dkr.ecr.${region}.amazonaws.com/${repo}:${Tag}"
    )
    $params = @{ commands = $commands } | ConvertTo-Json -Compress -Depth 10
    $tmpFile = [System.IO.Path]::GetTempFileName()
    $params | Out-File -FilePath $tmpFile -Encoding utf8 -NoNewline
    try {
        $sendOut = Invoke-AwsJson @("ssm", "send-command", "--instance-ids", $instanceId, "--document-name", "AWS-RunShellScript", "--parameters", "fileb://$tmpFile", "--region", $script:Region, "--output", "json")
        $cmdId = $sendOut.Command.CommandId
        if (-not $cmdId) { throw "SSM send-command failed for build" }
        $waitSec = 1200
        $elapsed = 0
        while ($elapsed -lt $waitSec) {
            Start-Sleep -Seconds 20
            $elapsed += 20
            $out = Invoke-AwsJson @("ssm", "get-command-invocation", "--command-id", $cmdId, "--instance-id", $instanceId, "--region", $script:Region, "--output", "json")
            $status = $out.Status
            if ($status -eq "Success") { Write-Ok "Build server push completed"; return }
            if ($status -eq "Failed" -or $status -eq "Cancelled") {
                $stderr = if ($out.StandardErrorContent) { $out.StandardErrorContent } else { "no stderr" }
                throw "Build server SSM command failed: $status. StandardError: $stderr"
            }
        }
        throw "Build server SSM command did not complete within ${waitSec}s."
    } finally {
        Remove-Item -Path $tmpFile -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-BootstrapEcrUri {
    if ($script:SkipBuild) {
        if (-not $script:EcrRepoUri -or $script:EcrRepoUri.Trim() -eq "") {
            $tag = Get-EcrResolvedTag
            if ($tag) {
                $reg = $script:Region
                $acc = $script:AccountId
                $script:EcrRepoUriResolved = "${acc}.dkr.ecr.${reg}.amazonaws.com/${script:VideoWorkerRepo}:${tag}"
                $script:EcrRepoUri = $script:EcrRepoUriResolved
                Write-Ok "ECR URI resolved (SkipBuild): $script:EcrRepoUriResolved"
            }
        }
        return
    }
    if ($script:EcrRepoUri -and $script:EcrRepoUri.Trim() -ne "") {
        if ($script:EcrRepoUri -match ':latest\s*$') { throw "Resolved ECR URI must not be :latest." }
        $script:EcrRepoUriResolved = $script:EcrRepoUri
        Write-Ok "ECR URI from parameter: $script:EcrRepoUriResolved"
        return
    }
    $tag = Get-EcrResolvedTag
    if (-not $tag) { $tag = "bootstrap-" + (Get-Date -Format "yyyyMMdd-HHmmss") }
    $reg = $script:Region
    $acc = $script:AccountId
    $uri = "${acc}.dkr.ecr.${reg}.amazonaws.com/${script:VideoWorkerRepo}:${tag}"
    $script:EcrRepoUriResolved = $uri
    $script:EcrRepoUri = $uri

    $exists = $null
    try {
        $img = Invoke-AwsJson @("ecr", "describe-images", "--repository-name", $script:VideoWorkerRepo, "--image-ids", "imageTag=$tag", "--region", $script:Region, "--output", "json")
        if ($img -and $img.imageDetails -and $img.imageDetails.Count -gt 0) { $exists = $true }
    } catch { }
    if ($exists) {
        Write-Ok "ECR image exists: $uri"
        return
    }
    Write-Host "  ECR image not found; triggering build server..." -ForegroundColor Yellow
    Invoke-BuildServerBuild -Tag $tag -Uri $uri
    Write-Ok "ECR URI resolved after build: $uri"
}

function Invoke-Bootstrap {
    param(
        [switch]$Bootstrap = $true,
        [switch]$SkipSqs = $false,
        [switch]$SkipRds = $false,
        [switch]$SkipRedis = $false,
        [switch]$SkipBuild = $false
    )
    if (-not $Bootstrap) { return }
    Write-Step "Bootstrap (one-take preparation)"
    $script:SkipSqs = $SkipSqs
    $script:SkipRds = $SkipRds
    $script:SkipRedis = $SkipRedis
    $script:SkipBuild = $SkipBuild

    Invoke-BootstrapSsmRdsPassword
    Invoke-BootstrapSqs
    Invoke-BootstrapRdsEngineVersion
    Invoke-BootstrapEcrUri

    Write-Ok "Bootstrap done"
}
