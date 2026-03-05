# Bootstrap: One-take 자동 준비. SSM password, SQS, RDS engineVersion, ECR resolve.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키 사용. 배포·검증 시 에이전트가 환경변수로 설정한 뒤 호출.
# deploy.ps1에서 Preflight 직후·Ensure 직전에 호출. params.yaml은 수정하지 않음.
$ErrorActionPreference = "Stop"

function Invoke-BootstrapWorkersEnv {
    if (-not $script:SsmWorkersEnv -or $script:SsmWorkersEnv.Trim() -eq "") { return }
    if ($script:PlanMode) { Write-Ok "Bootstrap workers env skipped (Plan)"; return }

    $existing = Invoke-AwsJson @("ssm", "get-parameter", "--name", $script:SsmWorkersEnv, "--region", $script:Region, "--output", "json")
    if ($existing -and $existing.Parameter -and $existing.Parameter.Name) {
        Write-Ok "SSM workers env already set: $($script:SsmWorkersEnv)"
        return
    }

    $repoRoot = (Get-Item $ScriptRoot).Parent.Parent.FullName
    $envPath = Join-Path $repoRoot ".env"
    if (-not (Test-Path -LiteralPath $envPath)) {
        Write-Warn "SSM $($script:SsmWorkersEnv) missing and .env not found at $envPath. Create .env and re-run, or run scripts/archive/infra/ssm_bootstrap_video_worker.ps1 -Region $($script:Region) -Overwrite."
        return
    }

    $requiredKeys = @("AWS_DEFAULT_REGION", "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT", "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_ENDPOINT", "R2_VIDEO_BUCKET", "API_BASE_URL", "INTERNAL_WORKER_TOKEN", "REDIS_HOST", "REDIS_PORT")
    $envHash = @{}
    $content = [System.IO.File]::ReadAllText($envPath, [System.Text.UTF8Encoding]::new($false))
    if ($content.Length -ge 1 -and $content[0] -eq [char]0xFEFF) { $content = $content.Substring(1) }
    foreach ($line in ($content -split "`r?`n")) {
        $line = $line.Trim()
        if ($line -match '^\s*#' -or $line -eq '') { continue }
        if ($line -match '^([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
            $key = $matches[1]; $val = $matches[2].Trim()
            if ($val -match '^["''](.*)["'']$') { $val = $matches[1] }
            $envHash[$key] = $val
        }
    }
    if (-not $envHash["AWS_DEFAULT_REGION"] -and $envHash["AWS_REGION"]) { $envHash["AWS_DEFAULT_REGION"] = $envHash["AWS_REGION"] }
    if (-not $envHash["DB_PORT"]) { $envHash["DB_PORT"] = "5432" }
    if (-not $envHash["REDIS_PORT"]) { $envHash["REDIS_PORT"] = "6379" }
    $envHash["DJANGO_SETTINGS_MODULE"] = "apps.api.config.settings.worker"
    $missing = @($requiredKeys | Where-Object { -not $envHash[$_] -or [string]$envHash[$_] -eq "" })
    if ($missing.Count -gt 0) {
        Write-Warn "SSM $($script:SsmWorkersEnv) missing; .env missing keys: $($missing -join ', '). Run ssm_bootstrap_video_worker.ps1 or fix .env."
        return
    }

    $obj = [ordered]@{}
    foreach ($k in @($requiredKeys) + "DJANGO_SETTINGS_MODULE") { $obj[$k] = $envHash[$k] }
    $json = $obj | ConvertTo-Json -Compress -Depth 10
    $jsonBytes = [System.Text.Encoding]::UTF8.GetBytes($json)
    $valueBase64 = [Convert]::ToBase64String($jsonBytes)
    Invoke-Aws @("ssm", "put-parameter", "--name", $script:SsmWorkersEnv, "--type", "SecureString", "--value", $valueBase64, "--overwrite", "--region", $script:Region) -ErrorMessage "put-parameter workers env" | Out-Null
    Write-Ok "SSM workers env created from .env: $($script:SsmWorkersEnv)"
    $script:ChangesMade = $true
}

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

    $msgDlq = "${msgName}-dlq"
    $aiDlq = "${aiName}-dlq"
    $msgVisibility = 900
    $aiVisibility = 3600
    $maxReceiveCount = 5

    foreach ($qName in @($msgName, $aiName)) {
        $url = $null
        $get = Invoke-AwsJson @("sqs", "get-queue-url", "--queue-name", $qName, "--region", $script:Region, "--output", "json")
        if ($get -and $get.QueueUrl) { $url = $get.QueueUrl }
        $dlqName = if ($qName -eq $msgName) { $msgDlq } else { $aiDlq }
        $visibility = if ($qName -eq $msgName) { $msgVisibility } else { $aiVisibility }

        if (-not (Invoke-AwsJson @("sqs", "get-queue-url", "--queue-name", $dlqName, "--region", $script:Region, "--output", "json") -ErrorAction SilentlyContinue).QueueUrl) {
            Invoke-AwsJson @("sqs", "create-queue", "--queue-name", $dlqName, "--region", $script:Region, "--output", "json") | Out-Null
            Write-Host "  SQS DLQ created: $dlqName" -ForegroundColor Green
            $script:ChangesMade = $true
        }
        $dlqUrl = (Invoke-AwsJson @("sqs", "get-queue-url", "--queue-name", $dlqName, "--region", $script:Region, "--output", "json")).QueueUrl
        $dlqArn = (Invoke-AwsJson @("sqs", "get-queue-attributes", "--queue-url", $dlqUrl, "--attribute-names", "QueueArn", "--region", $script:Region, "--output", "json")).Attributes.QueueArn

        if (-not $url) {
            $redrive = '{"deadLetterTargetArn":"' + $dlqArn + '","maxReceiveCount":"' + $maxReceiveCount + '"}'
            $redriveArg = 'RedrivePolicy="' + ($redrive.Replace('"', '\"')) + '"'
            Invoke-AwsJson @("sqs", "create-queue", "--queue-name", $qName, "--attributes", "VisibilityTimeout=$visibility", "MessageRetentionPeriod=1209600", $redriveArg, "--region", $script:Region, "--output", "json") | Out-Null
            $get = Invoke-AwsJson @("sqs", "get-queue-url", "--queue-name", $qName, "--region", $script:Region, "--output", "json")
            if ($get -and $get.QueueUrl) { $url = $get.QueueUrl; $script:ChangesMade = $true }
        } else {
            $attrs = (Invoke-AwsJson @("sqs", "get-queue-attributes", "--queue-url", $url, "--attribute-names", "All", "--region", $script:Region, "--output", "json")).Attributes
            $needsUpdate = $false
            $currentVis = if ($attrs.VisibilityTimeout) { [int]$attrs.VisibilityTimeout } else { 30 }
            if ($currentVis -lt $visibility) { $needsUpdate = $true }
            $currentRedrive = $attrs.RedrivePolicy
            if (-not $currentRedrive -or $currentRedrive -notmatch "deadLetterTargetArn") {
                $needsUpdate = $true
            }
            if ($needsUpdate) {
                $redrive = '{"deadLetterTargetArn":"' + $dlqArn + '","maxReceiveCount":"' + $maxReceiveCount + '"}'
                $body = '{"QueueUrl":"' + $url + '","Attributes":{"VisibilityTimeout":"' + $visibility + '","RedrivePolicy":"' + ($redrive.Replace('"', '\"')) + '"}}'
                Invoke-Aws @("sqs", "set-queue-attributes", "--cli-input-json", $body, "--region", $script:Region) -ErrorMessage "set-queue-attributes $qName" | Out-Null
                Write-Host "  SQS attributes set: $qName VisibilityTimeout=$visibility RedrivePolicy->DLQ" -ForegroundColor Yellow
                $script:ChangesMade = $true
            }
        }
        if ($url) {
            if ($qName -eq $msgName) { $script:MessagingSqsQueueName = $qName; $script:MessagingSqsQueueUrl = $url; Write-Ok "SQS Messaging: $qName -> $url (DLQ=$dlqName, VisibilityTimeout=${visibility}s)" }
            else { $script:AiSqsQueueName = $qName; $script:AiSqsQueueUrl = $url; Write-Ok "SQS AI: $qName -> $url (DLQ=$dlqName, VisibilityTimeout=${visibility}s)" }
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
    # SSM 에이전트 준비 대기 (신규 생성/시작 직후 send-command 실패 방지)
    try {
        Wait-SSMOnline -InstanceId $instanceId -Reg $script:Region -TimeoutSec 600
    } catch {
        Write-Warn "SSM wait failed: $_. Proceeding with send-command (may fail)."
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
        "cd $buildPath 2>/dev/null || cd `$HOME/academy 2>/dev/null || (echo Build repo path not found; exit 1)",
        "git fetch origin 2>/dev/null || true",
        "git checkout `$TAG 2>/dev/null || git pull 2>/dev/null || true",
        "aws ecr get-login-password --region $region | docker login --username AWS --password-stdin ${acc}.dkr.ecr.${region}.amazonaws.com",
        "docker build --platform linux/arm64 -t ${repo}:${Tag} -f Dockerfile.video . 2>&1 || docker build --platform linux/arm64 -t ${repo}:${Tag} . 2>&1",
        "docker tag ${repo}:${Tag} ${acc}.dkr.ecr.${region}.amazonaws.com/${repo}:${Tag}",
        "docker push ${acc}.dkr.ecr.${region}.amazonaws.com/${repo}:${Tag}"
    )
    $paramsJson = @{ commands = $commands } | ConvertTo-Json -Compress -Depth 10
    try {
        $sendOut = Invoke-AwsJson @("ssm", "send-command", "--instance-ids", $instanceId, "--document-name", "AWS-RunShellScript", "--parameters", $paramsJson, "--region", $script:Region, "--output", "json")
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
    } finally { }
}

function Invoke-BootstrapEcrUri {
    if ($script:SkipBuild) {
        if (-not $script:EcrRepoUri -or $script:EcrRepoUri.Trim() -eq "") {
            $tag = if ($script:EcrUseLatestTag) { "latest" } else { Get-EcrResolvedTag }
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
        if (($script:EcrRepoUri -match ':latest\s*$') -and -not $script:EcrUseLatestTag) { throw "Resolved ECR URI must not be :latest when useLatestTag is false." }
        $script:EcrRepoUriResolved = $script:EcrRepoUri
        Write-Ok "ECR URI from parameter: $script:EcrRepoUriResolved"
        return
    }
    $tag = if ($script:EcrUseLatestTag) { "latest" } else { Get-EcrResolvedTag }
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
    if ($script:SkipBuild) {
        Write-Warn "SkipBuild: ECR image not found; not triggering build server. Deploy continues (Netprobe/JobDef may need image). Pass -EcrRepoUri or push image to unblock."
        $script:EcrRepoUriResolved = $uri
        $script:EcrRepoUri = $uri
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

    Invoke-BootstrapWorkersEnv
    Invoke-BootstrapSsmRdsPassword
    Invoke-BootstrapSqs
    Invoke-BootstrapRdsEngineVersion
    Invoke-BootstrapEcrUri

    Write-Ok "Bootstrap done"
}
