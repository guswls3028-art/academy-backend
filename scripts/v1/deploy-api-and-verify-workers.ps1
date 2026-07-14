# ==============================================================================
# API 재배포 + 전체 워커 연결 검증 (docs/ssot/params.yaml)
# API ASG instance refresh → 헬스체크 대기 → 워커 연결 전체 검증
# Usage: pwsh scripts/v1/deploy-api-and-verify-workers.ps1 [-AwsProfile default] [-SkipRefresh]
# ==============================================================================
param(
    [string]$AwsProfile = "default",
    [switch]$SkipRefresh  # 이미 refresh 진행 중이면 검증만 실행
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$Region = "ap-northeast-2"

# --- Init ---
. (Join-Path $ScriptRoot "core\env.ps1")
if ($AwsProfile -and $AwsProfile.Trim() -ne "") {
    $env:AWS_PROFILE = $AwsProfile.Trim()
    if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = $Region }
}
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
. (Join-Path $ScriptRoot "resources\api.ps1")
. (Join-Path $ScriptRoot "resources\worker_userdata.ps1")
$null = Load-SSOT -Env "prod"

$report = @()
$allPass = $true
function Add-Result($stage, $item, $status, $detail) {
    $color = switch ($status) { "PASS" { "Green" } "WARN" { "Yellow" } "FAIL" { "Red" } default { "Gray" } }
    Write-Host "  [$status] $item — $detail" -ForegroundColor $color
    $script:report += [PSCustomObject]@{ Stage=$stage; Item=$item; Status=$status; Detail=$detail }
    if ($status -eq "FAIL") { $script:allPass = $false }
}

function Wait-InstanceRefreshTerminal {
    param(
        [Parameter(Mandatory = $true)][string]$AsgName,
        [Parameter(Mandatory = $true)][string]$RefreshId,
        [int]$TimeoutSec = 1800
    )
    $elapsed = 0
    while ($elapsed -lt $TimeoutSec) {
        $result = Invoke-AwsJson @(
            "autoscaling", "describe-instance-refreshes",
            "--auto-scaling-group-name", $AsgName,
            "--instance-refresh-ids", $RefreshId,
            "--region", $Region,
            "--output", "json"
        )
        $state = @($result.InstanceRefreshes)[0]
        if (-not $state) { throw "Instance refresh not found: $AsgName/$RefreshId" }
        Write-Host "    refresh=$RefreshId status=$($state.Status) complete=$($state.PercentageComplete)%" -ForegroundColor DarkGray
        if ([string]$state.Status -eq "Successful") { return $state }
        if ([string]$state.Status -in @("Failed", "Cancelled", "RollbackFailed", "RollbackSuccessful")) {
            throw "Instance refresh terminated as $($state.Status): $($state.StatusReason)"
        }
        Start-Sleep -Seconds 15
        $elapsed += 15
    }
    throw "Instance refresh timed out after ${TimeoutSec}s: $AsgName/$RefreshId"
}

function Assert-AsgRunningContainerDigests {
    param(
        [Parameter(Mandatory = $true)][string]$AsgName,
        [Parameter(Mandatory = $true)][string]$RepoName,
        [Parameter(Mandatory = $true)][string]$ContainerName,
        [Parameter(Mandatory = $true)][string]$ExpectedDigest
    )
    $asgResult = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $AsgName, "--region", $Region, "--output", "json")
    $asg = @($asgResult.AutoScalingGroups)[0]
    if (-not $asg) { throw "ASG not found: $AsgName" }
    $instanceIds = @($asg.Instances | Where-Object { $_.LifecycleState -eq "InService" -and $_.HealthStatus -eq "Healthy" } | ForEach-Object { $_.InstanceId })
    if ($instanceIds.Count -ne [int]$asg.DesiredCapacity) {
        throw "ASG healthy InService count does not equal desired capacity: $AsgName healthy=$($instanceIds.Count) desired=$($asg.DesiredCapacity)"
    }
    $expectedUri = "$($script:AccountId).dkr.ecr.$Region.amazonaws.com/${RepoName}@${ExpectedDigest}"
    foreach ($instanceId in $instanceIds) {
        $command = "set -e; IMAGE_ID=`$(docker inspect --format '{{.Image}}' '$ContainerName'); docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' `"`$IMAGE_ID`""
        $parameters = Convert-JsonArgToFileRef (@{ commands = @($command); executionTimeout = @("120") } | ConvertTo-Json -Compress)
        $parameterFile = $parameters -replace '^file://', ''
        try {
            $sent = Invoke-AwsJson @("ssm", "send-command", "--instance-ids", $instanceId, "--document-name", "AWS-RunShellScript", "--parameters", $parameters, "--timeout-seconds", "180", "--region", $Region, "--output", "json")
        } finally {
            Remove-TempFiles @($parameterFile)
        }
        $commandId = [string]$sent.Command.CommandId
        if (-not $commandId) { throw "SSM returned no command id for $AsgName/$instanceId" }
        $elapsed = 0
        do {
            Start-Sleep -Seconds 3
            $elapsed += 3
            $result = Invoke-AwsJson @("ssm", "get-command-invocation", "--command-id", $commandId, "--instance-id", $instanceId, "--region", $Region, "--output", "json")
            if ([string]$result.Status -eq "Success") { break }
            if ([string]$result.Status -in @("Failed", "Cancelled", "TimedOut", "Cancelling")) {
                throw "Runtime digest command failed: $AsgName/$instanceId status=$($result.Status) stderr=$($result.StandardErrorContent)"
            }
        } while ($elapsed -lt 120)
        if ([string]$result.Status -ne "Success") { throw "Runtime digest command timed out: $AsgName/$instanceId" }
        $actual = @(([string]$result.StandardOutputContent -split "`r?`n") | Where-Object { $_.Trim() -ne "" } | Sort-Object -Unique)
        if ($actual.Count -ne 1 -or $actual[0] -ne $expectedUri) {
            throw "Running container must report exactly one account/region/repository digest URI: $AsgName/$instanceId expected=$expectedUri actual=$($actual -join ',')"
        }
    }
    return $instanceIds.Count
}

function Get-AsgRuntimeImageEvidence {
    param(
        [Parameter(Mandatory = $true)][string]$AsgName,
        [Parameter(Mandatory = $true)][string]$RepoName
    )

    $asgResult = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $AsgName, "--region", $Region, "--output", "json")
    $asg = @($asgResult.AutoScalingGroups)[0]
    if (-not $asg) { throw "ASG not found: $AsgName" }
    $ltRef = $asg.LaunchTemplate
    if (-not $ltRef -or -not $ltRef.LaunchTemplateId) { throw "ASG does not use a direct Launch Template: $AsgName" }
    if ([string]$ltRef.Version -ne '$Latest') { throw "ASG must track Launch Template version `$Latest: $AsgName actual=$($ltRef.Version)" }

    $versionResult = Invoke-AwsJson @("ec2", "describe-launch-template-versions", "--launch-template-id", $ltRef.LaunchTemplateId, "--versions", '$Latest', "--region", $Region, "--output", "json")
    $version = @($versionResult.LaunchTemplateVersions)[0]
    $encoded = if ($version -and $version.LaunchTemplateData) { [string]$version.LaunchTemplateData.UserData } else { "" }
    if (-not $encoded) { throw "Launch Template userdata is empty: $AsgName" }
    try {
        $raw = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($encoded))
    } catch {
        throw "Launch Template userdata is not valid base64: $AsgName"
    }

    $pattern = "(?<uri>" + [regex]::Escape("$($script:AccountId).dkr.ecr.$Region.amazonaws.com/$RepoName") + "@(?<digest>sha256:[0-9a-f]{64}))"
    $matchesExact = @([regex]::Matches($raw, $pattern, [System.Text.RegularExpressions.RegexOptions]::IgnoreCase))
    $distinctUris = @($matchesExact | ForEach-Object { $_.Groups["uri"].Value.ToLowerInvariant() } | Sort-Object -Unique)
    if ($distinctUris.Count -ne 1) {
        throw "Launch Template must contain exactly one distinct account/region/repository digest URI: $AsgName repo=$RepoName actual=$($distinctUris -join ',')"
    }
    $match = $matchesExact[0]

    return [PSCustomObject]@{
        Image = $match.Groups["uri"].Value
        Digest = $match.Groups["digest"].Value.ToLowerInvariant()
        LaunchTemplateId = $ltRef.LaunchTemplateId
        Version = $version.VersionNumber
    }
}

function Get-BatchRuntimeImageEvidence {
    param(
        [Parameter(Mandatory = $true)][string]$JobDefinitionName,
        [Parameter(Mandatory = $true)][string]$RepoName
    )

    $result = Invoke-AwsJson @("batch", "describe-job-definitions", "--job-definition-name", $JobDefinitionName, "--status", "ACTIVE", "--region", $Region, "--output", "json")
    $definition = @($result.jobDefinitions | Sort-Object { [int]$_.revision } -Descending)[0]
    if (-not $definition) { throw "Active Batch job definition not found: $JobDefinitionName" }
    $image = [string]$definition.containerProperties.image
    $exactPattern = '^' + [regex]::Escape("$($script:AccountId).dkr.ecr.$Region.amazonaws.com/$RepoName") + '@(?<digest>sha256:[0-9a-f]{64})$'
    if ($image -notmatch $exactPattern) { throw "Batch job definition must use the exact account/region/repository digest URI: $JobDefinitionName image=$image" }
    $digest = $matches["digest"].ToLowerInvariant()
    $tag = ""
    if ($digest -notmatch '^sha256:[0-9a-f]{64}$') { throw "Cannot resolve Batch runtime digest: $JobDefinitionName image=$image" }

    return [PSCustomObject]@{
        Image = $image
        Digest = $digest.ToLowerInvariant()
        Tag = $tag
        Revision = $definition.revision
    }
}

$timestamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host " API Deploy + Worker Verification (docs/ssot/params.yaml)" -ForegroundColor Cyan
Write-Host " $timestamp" -ForegroundColor Cyan
Write-Host "============================================`n" -ForegroundColor Cyan

# ==============================================================================
# STAGE 0: Image Freshness Check (Git SHA vs CI Build vs ECR)
# ==============================================================================
Write-Host "=== STAGE 0: Image Freshness Check ===" -ForegroundColor Cyan

$repoRoot = Get-RepoRoot
$gitHeadSha = $null
$ciBuildSha = $null
$ciBuildDigests = @{}
$immutableTags = @{}
$imageRepos = @("academy-api", "academy-video-worker", "academy-messaging-worker", "academy-ai-worker-cpu", "academy-tools-worker", "academy-base")

# 0-1. Git HEAD SHA
try {
    Push-Location $repoRoot
    $null = git fetch origin 2>&1
    $gitHeadSha = (git rev-parse origin/main 2>&1).Trim()
    $gitShort = $gitHeadSha.Substring(0, 7)
    Pop-Location
    Add-Result "0-IMAGE" "git/HEAD" "PASS" "$gitShort ($gitHeadSha)"
} catch {
    Pop-Location
    Add-Result "0-IMAGE" "git/HEAD" "WARN" "git fetch failed: $_"
}

# 0-2. Latest CI build SHA + status (via gh CLI)
$ciRunStatus = $null
$ciRunConclusion = $null
$reportOnlyHead = $false
$runtimeImageUnchangedHead = $false
try {
    $ghOutput = gh run list --limit 10 --json databaseId,status,conclusion,headSha,workflowName 2>&1
    $ghRuns = $ghOutput | ConvertFrom-Json
    $buildRuns = @($ghRuns | Where-Object { $_.workflowName -match "Build and Push" })

    # Find latest completed successful build
    $latestSuccess = $buildRuns | Where-Object { $_.status -eq "completed" -and $_.conclusion -eq "success" } | Select-Object -First 1
    # Find any in-progress build
    $inProgress = $buildRuns | Where-Object { $_.status -eq "in_progress" } | Select-Object -First 1

    if ($inProgress) {
        $ciRunStatus = "in_progress"
        $ciBuildSha = $inProgress.headSha
        $ciShort = $ciBuildSha.Substring(0, 7)
        Add-Result "0-IMAGE" "ci/in-progress" "WARN" "Build in progress for $ciShort (run #$($inProgress.databaseId))"
    }

    if ($latestSuccess) {
        $lastSuccessSha = $latestSuccess.headSha
        $lastSuccessShort = $lastSuccessSha.Substring(0, 7)
        Add-Result "0-IMAGE" "ci/last-success" "PASS" "$lastSuccessShort (run #$($latestSuccess.databaseId))"

        # Check if git HEAD matches last successful build
        if ($gitHeadSha -and $gitHeadSha -ne $lastSuccessSha) {
            if ($inProgress -and $inProgress.headSha -eq $gitHeadSha) {
                Add-Result "0-IMAGE" "ci/HEAD-sync" "WARN" "HEAD=$($gitHeadSha.Substring(0,7)) != lastBuild=$lastSuccessShort (build in progress)"
            } else {
                $deltaFiles = @()
                try {
                    Push-Location $repoRoot
                    $deltaFiles = @(git diff --name-only "$lastSuccessSha..$gitHeadSha" 2>$null)
                    Pop-Location
                } catch {
                    Pop-Location
                    $deltaFiles = @()
                }
                $nonReportFiles = @($deltaFiles | Where-Object { $_ -ne "docs/reports/ci-build.latest.md" })
                $imageAffectingFiles = @($nonReportFiles | Where-Object {
                    $_ -match '^(\.dockerignore$|academy/|apps/|libs/|models/|scripts/|common/|manage\.py$|requirements|pyproject\.toml$|poetry\.lock$|Pipfile|Dockerfile|docker/)'
                })
                if ($deltaFiles.Count -gt 0 -and $nonReportFiles.Count -eq 0) {
                    $reportOnlyHead = $true
                    Add-Result "0-IMAGE" "ci/HEAD-sync" "PASS" "HEAD only contains CI build report after deployed build"
                } elseif ($nonReportFiles.Count -gt 0 -and $imageAffectingFiles.Count -eq 0) {
                    $runtimeImageUnchangedHead = $true
                    Add-Result "0-IMAGE" "ci/HEAD-sync" "PASS" "HEAD has no runtime image changes since last image build"
                } else {
                    Add-Result "0-IMAGE" "ci/HEAD-sync" "FAIL" "HEAD=$($gitHeadSha.Substring(0,7)) != lastBuild=$lastSuccessShort (no build running!)"
                }
            }
        } elseif ($gitHeadSha) {
            Add-Result "0-IMAGE" "ci/HEAD-sync" "PASS" "HEAD matches last successful build"
        }
    } else {
        Add-Result "0-IMAGE" "ci/last-success" "WARN" "No successful build found in recent runs"
    }
} catch {
    Add-Result "0-IMAGE" "ci/status" "WARN" "gh CLI failed: $_"
}

# 0-3. Last complete successful release manifest. A build report is only a
# candidate and must never authorize a manual deploy.
$ciBuildReportPath = Join-Path $repoRoot "docs\reports\release-manifest.latest.json"
$ciBuildReportSha = $null
if (Test-Path $ciBuildReportPath) {
    try {
        $releaseManifest = Get-Content -LiteralPath $ciBuildReportPath -Raw -Encoding UTF8 | ConvertFrom-Json
        if ([int]$releaseManifest.schemaVersion -ne 1 -or -not [bool]$releaseManifest.complete -or [string]$releaseManifest.status -ne "successful") {
            throw "release manifest is not complete/successful"
        }
        if (@($releaseManifest.images.PSObject.Properties).Count -ne $imageRepos.Count) {
            throw "release manifest must contain exactly $($imageRepos.Count) images"
        }
        $ciBuildReportSha = [string]$releaseManifest.gitSha
        foreach ($repo in $imageRepos) {
            $ciBuildDigests[$repo] = [string]$releaseManifest.images.PSObject.Properties[$repo].Value.digest
        }
        $ciReportShort = if ($ciBuildReportSha -and $ciBuildReportSha.Length -ge 7) { $ciBuildReportSha.Substring(0, 7) } elseif ($ciBuildReportSha) { $ciBuildReportSha } else { "unknown" }
        Add-Result "0-IMAGE" "release-manifest/sha" "PASS" "$ciReportShort ($($ciBuildDigests.Count) verified images)"
    } catch {
        Add-Result "0-IMAGE" "release-manifest" "FAIL" "parse failed: $_"
    }
} else {
    Add-Result "0-IMAGE" "release-manifest" "FAIL" "release-manifest.latest.json not found"
}

# 0-4. CI digest가 immutable sha-* tag로 ECR에 존재하는지 검증
foreach ($repo in $imageRepos) {
    try {
        $ciDigest = $ciBuildDigests[$repo]
        if ($ciDigest -notmatch '^sha256:[0-9a-f]{64}$') { throw "CI report digest missing or invalid: $repo" }
        $ecrResult = Invoke-AwsJson @("ecr", "describe-images", "--repository-name", $repo, "--image-ids", "imageDigest=$ciDigest", "--region", $Region, "--output", "json")
        $detail = @($ecrResult.imageDetails)[0]
        if (-not $detail -or [string]$detail.imageDigest -ne $ciDigest) { throw "CI digest not found in ECR: $repo@$ciDigest" }
        $shaTags = @($detail.imageTags | Where-Object { $_ -match '^sha-(?:[0-9a-f]{8,40}|[0-9a-f]{40}-run-[0-9]+-[0-9]+)$' } | Sort-Object)
        if ($shaTags.Count -eq 0) { throw "CI digest has no immutable sha-* tag: $repo@$ciDigest" }
        $immutableTags[$repo] = $shaTags[0]
        Add-Result "0-IMAGE" "ecr/$repo" "PASS" "$($shaTags[0]) -> $ciDigest (pushed $($detail.imagePushedAt))"
    } catch {
        Add-Result "0-IMAGE" "ecr/$repo" "FAIL" "$_"
    }
}

# 0-5. ASG Launch Template runtime targets: digest-pinned userdata vs CI digest
foreach ($target in @(
    @{ Repo="academy-api";              Asg=$script:ApiASGName;       Container="academy-api" },
    @{ Repo="academy-messaging-worker"; Asg=$script:MessagingASGName; Container="academy-messaging-worker" },
    @{ Repo="academy-ai-worker-cpu";    Asg=$script:AiASGName;        Container="academy-ai-worker-cpu" },
    @{ Repo="academy-tools-worker";     Asg=$script:ToolsASGName;     Container="academy-tools-worker" }
)) {
    try {
        $evidence = Get-AsgRuntimeImageEvidence -AsgName $target.Asg -RepoName $target.Repo
        $ciDigest = $ciBuildDigests[$target.Repo]
        if ($evidence.Digest -ne $ciDigest) { throw "runtime digest mismatch: actual=$($evidence.Digest) expected=$ciDigest" }
        Add-Result "0-IMAGE" "runtime/$($target.Repo)" "PASS" "LT=$($evidence.LaunchTemplateId):$($evidence.Version) $($immutableTags[$target.Repo]) -> $($evidence.Digest)"
    } catch {
        Add-Result "0-IMAGE" "runtime/$($target.Repo)" "FAIL" "$_"
    }
}

# Video Batch runtime targets: each active job definition must use sha-* or digest.
foreach ($jobDefinitionName in @($script:SSOT_JobDef | Where-Object { $_ })) {
    try {
        $evidence = Get-BatchRuntimeImageEvidence -JobDefinitionName $jobDefinitionName -RepoName "academy-video-worker"
        $ciDigest = $ciBuildDigests["academy-video-worker"]
        if ($evidence.Digest -ne $ciDigest) { throw "runtime digest mismatch: actual=$($evidence.Digest) expected=$ciDigest" }
        $immutableRef = if ($evidence.Tag) { $evidence.Tag } else { $evidence.Digest }
        Add-Result "0-IMAGE" "runtime/$jobDefinitionName" "PASS" "revision=$($evidence.Revision) $immutableRef -> $($evidence.Digest)"
    } catch {
        Add-Result "0-IMAGE" "runtime/$jobDefinitionName" "FAIL" "$_"
    }
}

# 0-6. Source/build coverage summary. Runtime equality is proven above from LT/jobdefs.
if ($gitHeadSha -and $ciBuildReportSha) {
    if ($gitHeadSha -eq $ciBuildReportSha) {
        Add-Result "0-IMAGE" "source/build" "PASS" "Git HEAD = CI image report SHA"
    } elseif ($reportOnlyHead) {
        Add-Result "0-IMAGE" "source/build" "PASS" "HEAD only contains the generated CI build report"
    } elseif ($runtimeImageUnchangedHead) {
        Add-Result "0-IMAGE" "source/build" "PASS" "HEAD has no image-affecting changes after the CI image report"
    } elseif ($ciRunStatus -eq "in_progress") {
        Add-Result "0-IMAGE" "source/build" "WARN" "New image build is in progress"
    } else {
        $runtimeDeltaFiles = @()
        $runtimeImageFiles = @()
        try {
            Push-Location $repoRoot
            $runtimeDeltaFiles = @(git diff --name-only "$ciBuildReportSha..$gitHeadSha" 2>$null)
            Pop-Location
            $runtimeImageFiles = @($runtimeDeltaFiles | Where-Object {
                $_ -match '^(\.dockerignore$|academy/|apps/|libs/|models/|scripts/|common/|manage\.py$|requirements|pyproject\.toml$|poetry\.lock$|Pipfile|Dockerfile|docker/)'
            })
        } catch { Pop-Location }
        if ($runtimeDeltaFiles.Count -gt 0 -and $runtimeImageFiles.Count -eq 0) {
            Add-Result "0-IMAGE" "source/build" "PASS" "Later HEAD changes do not affect runtime images"
        } else {
            $commitsBehind = 0
            try {
                Push-Location $repoRoot
                $commitsBehind = [int](git rev-list --count "$ciBuildReportSha..origin/main" 2>&1)
                Pop-Location
            } catch { Pop-Location }
            Add-Result "0-IMAGE" "source/build" "WARN" "CI image report is $commitsBehind commit(s) behind HEAD"
        }
    }
}

if (-not $allPass) {
    Write-Host "`nStage 0 immutable image evidence failed; refusing to start an API instance refresh." -ForegroundColor Red
    exit 1
}

# ==============================================================================
# STAGE 1: API ASG Instance Refresh
# ==============================================================================
Write-Host "=== STAGE 1: API ASG Instance Refresh ===" -ForegroundColor Cyan

$refreshId = ""
if (-not $SkipRefresh) {
    $minHealthy = if ($script:ApiInstanceRefreshMinHealthyPercentage -gt 0) { $script:ApiInstanceRefreshMinHealthyPercentage } else { 100 }
    $warmup = if ($script:ApiInstanceRefreshInstanceWarmup -gt 0) { $script:ApiInstanceRefreshInstanceWarmup } else { 300 }
    $prefs = Convert-JsonArgToFileRef (@{MinHealthyPercentage=$minHealthy;MaxHealthyPercentage=200;InstanceWarmup=$warmup} | ConvertTo-Json -Compress)
    $prefsFile = $prefs -replace '^file://', ''

    Write-Host "  Starting instance refresh: $($script:ApiASGName) (MinHealthy=$minHealthy%, Warmup=${warmup}s)" -ForegroundColor White
    try {
        $refreshResult = Invoke-Aws @("autoscaling", "start-instance-refresh",
            "--auto-scaling-group-name", $script:ApiASGName,
            "--preferences", $prefs,
            "--region", $Region) -ErrorMessage "start-instance-refresh"
        $refreshId = ($refreshResult | ConvertFrom-Json).InstanceRefreshId
        Add-Result "1-REFRESH" "instance-refresh-start" "PASS" "RefreshId=$refreshId"
    } catch {
        if ($_.Exception.Message -match "InstanceRefreshInProgress") {
            $active = Invoke-AwsJson @("autoscaling", "describe-instance-refreshes", "--auto-scaling-group-name", $script:ApiASGName, "--region", $Region, "--output", "json")
            $current = @($active.InstanceRefreshes | Where-Object { $_.Status -in @("Pending", "InProgress", "Cancelling", "RollbackInProgress") })[0]
            if (-not $current) { throw "AWS reported InstanceRefreshInProgress but no active refresh could be resolved." }
            $refreshId = [string]$current.InstanceRefreshId
            Add-Result "1-REFRESH" "instance-refresh-start" "PASS" "Already in progress: RefreshId=$refreshId"
        } else {
            Add-Result "1-REFRESH" "instance-refresh-start" "FAIL" "$_"
        }
    } finally {
        Remove-TempFiles @($prefsFile)
    }
} else {
    Write-Host "  -SkipRefresh: no new refresh will be started" -ForegroundColor Yellow
    $recent = Invoke-AwsJson @("autoscaling", "describe-instance-refreshes", "--auto-scaling-group-name", $script:ApiASGName, "--region", $Region, "--output", "json")
    $current = @($recent.InstanceRefreshes | Where-Object { $_.Status -in @("Pending", "InProgress", "Cancelling", "RollbackInProgress") })[0]
    if ($current) { $refreshId = [string]$current.InstanceRefreshId }
}

if ($refreshId) {
    try {
        $refreshState = Wait-InstanceRefreshTerminal -AsgName $script:ApiASGName -RefreshId $refreshId -TimeoutSec 1800
        Add-Result "1-REFRESH" "refresh-status" "PASS" "Successful (100%)"
    } catch {
        Add-Result "1-REFRESH" "refresh-status" "FAIL" "$_"
    }
} else {
    Add-Result "1-REFRESH" "refresh-status" "PASS" "No active refresh (-SkipRefresh)"
}

# ==============================================================================
# STAGE 2: API Health Check (wait up to 10 min)
# ==============================================================================
Write-Host "`n=== STAGE 2: API Health Check ===" -ForegroundColor Cyan

$healthzUrl = "https://api.hakwonplus.com/healthz"
$healthUrl = "https://api.hakwonplus.com/health"
$maxWait = 600
$elapsed = 0
$healthzOk = $false

Write-Host "  Waiting for /healthz 200 (max ${maxWait}s)..." -ForegroundColor White
while ($elapsed -lt $maxWait) {
    try {
        $resp = Invoke-WebRequest -Uri $healthzUrl -UseBasicParsing -TimeoutSec 10 -ErrorAction SilentlyContinue
        if ($resp.StatusCode -eq 200) { $healthzOk = $true; break }
    } catch { }
    Start-Sleep -Seconds 15
    $elapsed += 15
    Write-Host "    ... ${elapsed}s elapsed" -ForegroundColor DarkGray
}

if ($healthzOk) {
    Add-Result "2-HEALTH" "/healthz" "PASS" "200 OK (${elapsed}s)"
} else {
    Add-Result "2-HEALTH" "/healthz" "FAIL" "Not 200 after ${maxWait}s"
}

# /health (readiness)
try {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $resp2 = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 10 -ErrorAction Stop
    $sw.Stop()
    $ms = $sw.ElapsedMilliseconds
    if ($resp2.StatusCode -eq 200) {
        $status = if ($ms -gt 2000) { "WARN" } else { "PASS" }
        Add-Result "2-HEALTH" "/health" $status "200 OK (${ms}ms)"
    } else {
        Add-Result "2-HEALTH" "/health" "FAIL" "$($resp2.StatusCode)"
    }
} catch {
    Add-Result "2-HEALTH" "/health" "FAIL" "$_"
}

# ==============================================================================
# STAGE 3: API ASG Instance Status
# ==============================================================================
Write-Host "`n=== STAGE 3: API ASG Instance Status ===" -ForegroundColor Cyan

try {
    $asgJson = Invoke-Aws @("autoscaling", "describe-auto-scaling-groups",
        "--auto-scaling-group-names", $script:ApiASGName,
        "--query", "AutoScalingGroups[0].{Min:MinSize,Desired:DesiredCapacity,Max:MaxSize,Instances:Instances[*].[InstanceId,HealthStatus,LifecycleState]}",
        "--region", $Region) -ErrorMessage "describe-asg"
    $asg = $asgJson | ConvertFrom-Json
    $inService = @($asg.Instances | Where-Object { $_[2] -eq "InService" -and $_[1] -eq "Healthy" })
    $desired = [int]$asg.Desired
    if ($inService.Count -eq $desired) {
        Add-Result "3-ASG" "api-asg" "PASS" "Healthy InService=$($inService.Count) = desired=$desired (Min=$($asg.Min), Max=$($asg.Max))"
    } else {
        Add-Result "3-ASG" "api-asg" "FAIL" "Healthy InService=$($inService.Count) != desired=$desired"
    }
    foreach ($inst in $asg.Instances) {
        Add-Result "3-ASG" "  $($inst[0])" $(if ($inst[1] -eq "Healthy" -and $inst[2] -eq "InService") { "PASS" } else { "WARN" }) "$($inst[1])/$($inst[2])"
    }
} catch {
    Add-Result "3-ASG" "api-asg" "FAIL" "$_"
}

# ==============================================================================
# STAGE 3.1: Actual running container digests (after refresh terminal success)
# ==============================================================================
Write-Host "`n=== STAGE 3.1: Running Container Digests ===" -ForegroundColor Cyan
foreach ($target in @(
    @{ Repo="academy-api";              Asg=$script:ApiASGName;       Container="academy-api" },
    @{ Repo="academy-messaging-worker"; Asg=$script:MessagingASGName; Container="academy-messaging-worker" },
    @{ Repo="academy-ai-worker-cpu";    Asg=$script:AiASGName;        Container="academy-ai-worker-cpu" },
    @{ Repo="academy-tools-worker";     Asg=$script:ToolsASGName;     Container="academy-tools-worker" }
)) {
    try {
        $expectedDigest = $ciBuildDigests[$target.Repo]
        $runningCount = Assert-AsgRunningContainerDigests -AsgName $target.Asg -RepoName $target.Repo -ContainerName $target.Container -ExpectedDigest $expectedDigest
        Add-Result "3.1-RUNTIME" $target.Repo "PASS" "$runningCount healthy InService container(s) match $expectedDigest"
    } catch {
        Add-Result "3.1-RUNTIME" $target.Repo "FAIL" "$_"
    }
}

# ==============================================================================
# STAGE 3.5: DB Migration Drift Check (api-side schema vs container code)
# ==============================================================================
# 컨테이너가 health 200을 반환해도 0015 같은 신규 migration 미적용 시 ORM 쿼리에서
# 컬럼 부재로 깨질 수 있음 (2026-05-12 thumbnail_r2_key 사고). showmigrations 으로
# 모든 [X] 적용 상태인지 확인하고 [ ] 미적용 1건이라도 발견되면 FAIL.
Write-Host "`n=== STAGE 3.5: DB Migration Drift Check ===" -ForegroundColor Cyan

try {
    $apiIds = @(Get-APIASGInstanceIds | Where-Object { $_ -and $_.Trim() -ne "" })
    if (-not $apiIds -or $apiIds.Count -eq 0) {
        Add-Result "3.5-MIGRATE" "showmigrations" "WARN" "No API EC2 to query"
    } else {
        $targetId = $apiIds[0]
        $migrateScript = @'
sudo docker exec academy-api python manage.py showmigrations 2>&1 | grep -E "^ \[ \]" | head -20
'@
        $cmdJson = @{ commands = @($migrateScript) } | ConvertTo-Json -Compress
        $cmdId = Invoke-Aws @("ssm", "send-command",
            "--instance-ids", $targetId,
            "--document-name", "AWS-RunShellScript",
            "--parameters", $cmdJson,
            "--query", "Command.CommandId",
            "--output", "text",
            "--region", $Region) -ErrorMessage "ssm-send-migrate-check"
        Start-Sleep -Seconds 8
        $migOut = Invoke-Aws @("ssm", "get-command-invocation",
            "--command-id", $cmdId.Trim(),
            "--instance-id", $targetId,
            "--query", "StandardOutputContent",
            "--output", "text",
            "--region", $Region) -ErrorMessage "ssm-get-migrate-result"
        $unapplied = @($migOut -split "`n" | Where-Object { $_ -match "^ \[ \]" })
        if ($unapplied.Count -eq 0) {
            Add-Result "3.5-MIGRATE" "showmigrations" "PASS" "no pending migrations"
        } else {
            $detail = ($unapplied | Select-Object -First 5) -join "; "
            Add-Result "3.5-MIGRATE" "showmigrations" "FAIL" "$($unapplied.Count) unapplied: $detail"
        }
    }
} catch {
    Add-Result "3.5-MIGRATE" "showmigrations" "WARN" "$_"
}

# ==============================================================================
# STAGE 4: Worker ASG Status
# ==============================================================================
Write-Host "`n=== STAGE 4: Worker ASG Status ===" -ForegroundColor Cyan

foreach ($worker in @(
    @{ Name="messaging"; AsgName=$script:MessagingWorkerASGName },
    @{ Name="ai";        AsgName=$script:AiWorkerASGName },
    @{ Name="tools";     AsgName=$script:ToolsASGName }
)) {
    $asgName = $worker.AsgName
    if (-not $asgName) { $asgName = "academy-v1-$($worker.Name)-worker-asg" }
    try {
        $wJson = Invoke-Aws @("autoscaling", "describe-auto-scaling-groups",
            "--auto-scaling-group-names", $asgName,
            "--query", "AutoScalingGroups[0].{Desired:DesiredCapacity,InService:length(Instances[?LifecycleState=='InService']),Min:MinSize,Max:MaxSize}",
            "--region", $Region) -ErrorMessage "describe-$($worker.Name)-asg"
        $w = $wJson | ConvertFrom-Json
        if ($null -eq $w -or $null -eq $w.Desired) {
            Add-Result "4-WORKERS" "$($worker.Name)-asg" "FAIL" "ASG not found: $asgName"
        } else {
            $status = if ($w.InService -eq $w.Desired) { "PASS" } else { "FAIL" }
            Add-Result "4-WORKERS" "$($worker.Name)-asg" $status "InService=$($w.InService)/Desired=$($w.Desired) (idle=0 정상)"
        }
    } catch {
        Add-Result "4-WORKERS" "$($worker.Name)-asg" "FAIL" "$_"
    }
}

# ==============================================================================
# STAGE 5: SQS Queue Connectivity
# ==============================================================================
Write-Host "`n=== STAGE 5: SQS Queue Connectivity ===" -ForegroundColor Cyan

foreach ($q in @(
    @{ Name="messaging"; QueueName="academy-v1-messaging-queue" },
    @{ Name="ai";        QueueName="academy-v1-ai-queue" }
)) {
    try {
        $urlJson = Invoke-Aws @("sqs", "get-queue-url",
            "--queue-name", $q.QueueName,
            "--region", $Region) -ErrorMessage "get-queue-url-$($q.Name)"
        $qUrl = ($urlJson | ConvertFrom-Json).QueueUrl

        $attrJson = Invoke-Aws @("sqs", "get-queue-attributes",
            "--queue-url", $qUrl,
            "--attribute-names", "All",
            "--region", $Region) -ErrorMessage "get-queue-attributes-$($q.Name)"
        $attrs = ($attrJson | ConvertFrom-Json).Attributes

        $visible = [int]$attrs.ApproximateNumberOfMessages
        $inFlight = [int]$attrs.ApproximateNumberOfMessagesNotVisible
        $visTimeout = $attrs.VisibilityTimeout

        $status = if ($visible -le 100) { "PASS" } else { "WARN" }
        Add-Result "5-SQS" "$($q.Name)-main" $status "Visible=$visible, InFlight=$inFlight, VisTimeout=${visTimeout}s"
    } catch {
        Add-Result "5-SQS" "$($q.Name)-main" "FAIL" "$_"
    }

    # DLQ
    try {
        $dlqUrlJson = Invoke-Aws @("sqs", "get-queue-url",
            "--queue-name", "$($q.QueueName)-dlq",
            "--region", $Region) -ErrorMessage "get-dlq-url-$($q.Name)"
        $dlqUrl = ($dlqUrlJson | ConvertFrom-Json).QueueUrl

        $dlqAttrJson = Invoke-Aws @("sqs", "get-queue-attributes",
            "--queue-url", $dlqUrl,
            "--attribute-names", "All",
            "--region", $Region) -ErrorMessage "get-dlq-attributes-$($q.Name)"
        $dlqAttrs = ($dlqAttrJson | ConvertFrom-Json).Attributes
        $dlqVisible = [int]$dlqAttrs.ApproximateNumberOfMessages

        $status = if ($dlqVisible -eq 0) { "PASS" } elseif ($dlqVisible -le 5) { "WARN" } else { "FAIL" }
        Add-Result "5-SQS" "$($q.Name)-dlq" $status "DLQ=$dlqVisible"
    } catch {
        Add-Result "5-SQS" "$($q.Name)-dlq" "WARN" "DLQ not found or error: $_"
    }
}

# ==============================================================================
# STAGE 6: Video Batch Connectivity
# ==============================================================================
Write-Host "`n=== STAGE 6: Video Batch Connectivity ===" -ForegroundColor Cyan

# SSM VIDEO_BATCH_* env verification
try {
    $ssmRaw = Invoke-Aws @("ssm", "get-parameter",
        "--name", "/academy/api/env",
        "--with-decryption",
        "--query", "Parameter.Value",
        "--output", "text",
        "--region", $Region) -ErrorMessage "ssm-get-api-env"

    $ssmJson = $ssmRaw
    if ($ssmRaw -match '^[A-Za-z0-9+/]+=*$') {
        try { $ssmJson = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($ssmRaw)) } catch { }
    }
    $ssmObj = $ssmJson | ConvertFrom-Json

    # long path 폐기 (2026-05-10): VIDEO_BATCH_JOB_QUEUE_LONG / *_LONG 키는 검증 대상 아님.
    $batchKeys = @{
        "VIDEO_BATCH_JOB_QUEUE"          = "academy-v1-video-batch-queue"
        "VIDEO_BATCH_JOB_DEFINITION"     = "academy-v1-video-batch-jobdef"
    }
    foreach ($kv in $batchKeys.GetEnumerator()) {
        $actual = $ssmObj.PSObject.Properties[$kv.Key].Value
        if ($actual -eq $kv.Value) {
            Add-Result "6-BATCH" "ssm/$($kv.Key)" "PASS" "$actual"
        } else {
            Add-Result "6-BATCH" "ssm/$($kv.Key)" "FAIL" "actual='$actual' expected='$($kv.Value)'"
        }
    }

    # REDIS_HOST check
    $redisHost = $ssmObj.PSObject.Properties["REDIS_HOST"].Value
    if ($redisHost) {
        Add-Result "6-BATCH" "ssm/REDIS_HOST" "PASS" "$redisHost"
    } else {
        Add-Result "6-BATCH" "ssm/REDIS_HOST" "FAIL" "missing"
    }
} catch {
    Add-Result "6-BATCH" "ssm-api-env" "FAIL" "$_"
}

# Batch queue status
$batchQueues = @(
    @{ Name="standard"; Queue=$script:VideoQueueName },
    @{ Name="ops";      Queue=$script:OpsQueueName }
)
if ($script:VideoLongQueueName) {
    $batchQueues += @{ Name="long"; Queue=$script:VideoLongQueueName }
}
foreach ($bq in $batchQueues) {
    try {
        $bqJson = Invoke-Aws @("batch", "describe-job-queues",
            "--job-queues", $bq.Queue,
            "--query", "jobQueues[0].{state:state,status:status}",
            "--region", $Region) -ErrorMessage "batch-queue-$($bq.Name)"
        $bqState = $bqJson | ConvertFrom-Json
        if ($bqState.state -eq "ENABLED" -and $bqState.status -eq "VALID") {
            Add-Result "6-BATCH" "queue/$($bq.Name)" "PASS" "ENABLED/VALID"
        } else {
            Add-Result "6-BATCH" "queue/$($bq.Name)" "FAIL" "$($bqState.state)/$($bqState.status)"
        }
    } catch {
        Add-Result "6-BATCH" "queue/$($bq.Name)" "FAIL" "$_"
    }
}

# Batch CE status
$batchComputeEnvironments = @(
    @{ Name="standard"; CE=$script:VideoCEName },
    @{ Name="ops";      CE=$script:OpsCEName }
)
if ($script:VideoLongCEName) {
    $batchComputeEnvironments += @{ Name="long"; CE=$script:VideoLongCEName }
}
foreach ($ce in $batchComputeEnvironments) {
    try {
        $ceJson = Invoke-Aws @("batch", "describe-compute-environments",
            "--compute-environments", $ce.CE,
            "--query", "computeEnvironments[0].{state:state,status:status}",
            "--region", $Region) -ErrorMessage "batch-ce-$($ce.Name)"
        $ceState = $ceJson | ConvertFrom-Json
        if ($ceState.state -eq "ENABLED" -and $ceState.status -eq "VALID") {
            Add-Result "6-BATCH" "ce/$($ce.Name)" "PASS" "ENABLED/VALID"
        } else {
            Add-Result "6-BATCH" "ce/$($ce.Name)" "FAIL" "$($ceState.state)/$($ceState.status)"
        }
    } catch {
        Add-Result "6-BATCH" "ce/$($ce.Name)" "FAIL" "$_"
    }
}

# ==============================================================================
# STAGE 7: Workers SSM Env Verification
# ==============================================================================
Write-Host "`n=== STAGE 7: Workers SSM Env Verification ===" -ForegroundColor Cyan

try {
    $wEnvRaw = Invoke-Aws @("ssm", "get-parameter",
        "--name", "/academy/workers/env",
        "--with-decryption",
        "--query", "Parameter.Value",
        "--output", "text",
        "--region", $Region) -ErrorMessage "ssm-get-workers-env"

    $wEnvJson = $wEnvRaw
    try { $wEnvJson = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($wEnvRaw)) } catch { }
    $wEnvObj = $wEnvJson | ConvertFrom-Json

    $requiredKeys = @(
        "DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_PORT",
        "REDIS_HOST", "REDIS_PORT",
        "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_ENDPOINT",
        "API_BASE_URL", "INTERNAL_WORKER_TOKEN", "MESSAGING_TENANT_BINDING_KEY",
        "DJANGO_SETTINGS_MODULE"
    )
    foreach ($k in $requiredKeys) {
        $v = $wEnvObj.PSObject.Properties[$k].Value
        if ($v -and $v.Trim() -ne "") {
            $display = if ($k -match "PASSWORD|SECRET|TOKEN|KEY") { "***" } else { $v }
            Add-Result "7-WENV" "workers/$k" "PASS" "$display"
        } else {
            Add-Result "7-WENV" "workers/$k" "FAIL" "missing or empty"
        }
    }

    # Messaging-specific
    $mqName = $wEnvObj.PSObject.Properties["MESSAGING_SQS_QUEUE_NAME"].Value
    if ($mqName -eq "academy-v1-messaging-queue") {
        Add-Result "7-WENV" "workers/MESSAGING_SQS_QUEUE_NAME" "PASS" "$mqName"
    } elseif ($mqName) {
        Add-Result "7-WENV" "workers/MESSAGING_SQS_QUEUE_NAME" "WARN" "$mqName (expected academy-v1-messaging-queue)"
    } else {
        Add-Result "7-WENV" "workers/MESSAGING_SQS_QUEUE_NAME" "WARN" "not set"
    }
} catch {
    Add-Result "7-WENV" "workers-env" "FAIL" "$_"
}

# ==============================================================================
# STAGE 8: EventBridge Rules
# ==============================================================================
Write-Host "`n=== STAGE 8: EventBridge Rules ===" -ForegroundColor Cyan

foreach ($rule in @(
    @{ Name="reconcile";       RuleName="academy-v1-reconcile-video-jobs" },
    @{ Name="scan-stuck";      RuleName="academy-v1-video-scan-stuck-rate" },
    @{ Name="enqueue-uploaded"; RuleName="academy-v1-enqueue-uploaded-videos" }
)) {
    try {
        $ruleJson = Invoke-Aws @("events", "describe-rule",
            "--name", $rule.RuleName,
            "--region", $Region) -ErrorMessage "eventbridge-$($rule.Name)"
        $ruleObj = $ruleJson | ConvertFrom-Json
        if ($ruleObj.State -eq "ENABLED") {
            Add-Result "8-EVENTS" $rule.Name "PASS" "ENABLED — $($ruleObj.ScheduleExpression)"
        } else {
            Add-Result "8-EVENTS" $rule.Name "WARN" "$($ruleObj.State)"
        }
    } catch {
        Add-Result "8-EVENTS" $rule.Name "WARN" "$_"
    }
}

# ==============================================================================
# FINAL REPORT
# ==============================================================================
Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host " VERIFICATION REPORT" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

$passCount = ($report | Where-Object { $_.Status -eq "PASS" }).Count
$warnCount = ($report | Where-Object { $_.Status -eq "WARN" }).Count
$failCount = ($report | Where-Object { $_.Status -eq "FAIL" }).Count

Write-Host "`n  PASS: $passCount  |  WARN: $warnCount  |  FAIL: $failCount" -ForegroundColor $(if ($failCount -gt 0) { "Red" } elseif ($warnCount -gt 0) { "Yellow" } else { "Green" })

$verdict = if ($failCount -gt 0) { "FAIL" } elseif ($warnCount -gt 0) { "WARNING" } else { "PASS" }
Write-Host "`n  VERDICT: $verdict" -ForegroundColor $(if ($verdict -eq "FAIL") { "Red" } elseif ($verdict -eq "WARNING") { "Yellow" } else { "Green" })

# Save report
$reportDir = Join-Path (Get-RepoRoot) "docs\reports"
if (-not (Test-Path $reportDir)) { New-Item -ItemType Directory -Path $reportDir -Force | Out-Null }
$reportPath = Join-Path $reportDir "api-deploy-worker-verify.latest.md"

$md = @"
# API Deploy + Worker Verification Report

**Generated:** $timestamp
**SSOT:** docs/ssot/params.yaml
**Verdict:** $verdict (PASS=$passCount, WARN=$warnCount, FAIL=$failCount)

---

| Stage | Item | Status | Detail |
|-------|------|--------|--------|
"@

foreach ($r in $report) {
    $md += "`n| $($r.Stage) | $($r.Item) | **$($r.Status)** | $($r.Detail) |"
}

$md += @"

---

**SSOT Reference:** ``docs/ssot/params.yaml``; architecture context: ``docs/infrastructure/deployment-architecture.md``
"@

$md | Out-File -FilePath $reportPath -Encoding UTF8 -Force
Write-Host "`n  Report saved: $reportPath" -ForegroundColor DarkGray

if ($verdict -eq "FAIL") { exit 1 }
exit 0
