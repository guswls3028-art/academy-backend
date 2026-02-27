# Deep Batch diagnosis: RUNNABLE jobs, CE, ASG, ECS, networking.
# Run from repo root: .\scripts\diagnose_batch_deep.ps1
# Uses queue academy-video-batch-queue and CE academy-video-batch-ce-v2 (or from .env).
$ErrorActionPreference = "Stop"
$ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptRoot
Set-Location $RepoRoot

$Region = $env:AWS_REGION; if (-not $Region) { $Region = $env:AWS_DEFAULT_REGION }; if (-not $Region) { $Region = "ap-northeast-2" }
$QueueName = "academy-video-batch-queue"
$CeName = "academy-video-batch-ce-v2"
if (Test-Path (Join-Path $RepoRoot ".env")) {
    $envContent = Get-Content (Join-Path $RepoRoot ".env") -Raw
    if ($envContent -match 'VIDEO_BATCH_JOB_QUEUE=(\S+)') { $QueueName = $Matches[1].Trim() }
}

function ExecJson($argsArray) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $out = & aws @argsArray 2>&1
    $exit = $LASTEXITCODE
    $ErrorActionPreference = $prev
    if ($exit -ne 0) { return $null }
    if (-not $out) { return $null }
    try { return ($out | ConvertFrom-Json) } catch { return $null }
}

$callerArn = aws sts get-caller-identity --query Arn --output text 2>&1
if ($LASTEXITCODE -eq 0 -and $callerArn -match ":root") {
    Write-Host "BLOCK: root credentials. Use IAM user/role." -ForegroundColor Red
    exit 3
}

Write-Host "`n========== DIAGNOSE BATCH DEEP (queue=$QueueName CE=$CeName region=$Region) ==========" -ForegroundColor Cyan

# --- 1) RUNNABLE jobs: describe-jobs ---
Write-Host "`n--- 1) RUNNABLE jobs (describe-jobs) ---" -ForegroundColor Yellow
$listRunnable = ExecJson @("batch", "list-jobs", "--job-queue", $QueueName, "--job-status", "RUNNABLE", "--region", $Region)
$runnableIds = @()
if ($listRunnable -and $listRunnable.jobSummaryList) {
    $runnableIds = $listRunnable.jobSummaryList | ForEach-Object { $_.jobId }
}
if ($runnableIds.Count -eq 0) {
    Write-Host "No RUNNABLE jobs."
} else {
    $desc = ExecJson @("batch", "describe-jobs", "--jobs", ($runnableIds -join ","), "--region", $Region)
    if ($desc -and $desc.jobs) {
        foreach ($j in $desc.jobs) {
            Write-Host "  jobId=$($j.jobId) status=$($j.status) statusReason=$($j.statusReason) createdAt=$($j.createdAt)"
            Write-Host "    jobDefinition=$($j.jobDefinition) attempts=$($j.attempts)"
            if ($j.ecsProperties) { Write-Host "    ecsProperties: $($j.ecsProperties | ConvertTo-Json -Compress)" }
        }
    }
}

# --- 2) CE: describe-compute-environments ---
Write-Host "`n--- 2) Compute environment $CeName ---" -ForegroundColor Yellow
$ceDesc = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $CeName, "--region", $Region)
if (-not $ceDesc -or -not $ceDesc.computeEnvironments -or $ceDesc.computeEnvironments.Count -eq 0) {
    Write-Host "CE $CeName not found. Trying queue's first CE..."
    $jq = ExecJson @("batch", "describe-job-queues", "--job-queues", $QueueName, "--region", $Region)
    if ($jq -and $jq.jobQueues -and $jq.jobQueues[0].computeEnvironmentOrder -and $jq.jobQueues[0].computeEnvironmentOrder.Count -gt 0) {
        $ceArn = $jq.jobQueues[0].computeEnvironmentOrder[0].computeEnvironment
        $CeName = ($ceArn -split "/")[-1]
        if (-not $CeName) { $CeName = ($ceArn -split ":")[-1] }
        $ceDesc = ExecJson @("batch", "describe-compute-environments", "--compute-environments", $CeName, "--region", $Region)
    }
}
if ($ceDesc -and $ceDesc.computeEnvironments -and $ceDesc.computeEnvironments.Count -gt 0) {
    $ce = $ceDesc.computeEnvironments[0]
    $cr = $ce.computeResources
    Write-Host "  state=$($ce.state) status=$($ce.status) type=$($ce.type)"
    Write-Host "  allocationStrategy=$($cr.allocationStrategy) instanceTypes=$($cr.instanceTypes)"
    Write-Host "  minvCpus=$($cr.minvCpus) maxvCpus=$($cr.maxvCpus) desiredvCpus=$($cr.desiredvCpus)"
    Write-Host "  instanceRole=$($cr.instanceRole) instanceTypes=$($cr.instanceTypes)"
    if ($cr.launchTemplate) { Write-Host "  launchTemplate: id=$($cr.launchTemplate.launchTemplateId) version=$($cr.launchTemplate.launchTemplateVersion)" }
    Write-Host "  securityGroupIds=$($cr.securityGroupIds)"
    Write-Host "  subnets=$($cr.subnets)"
    Write-Host "  ec2KeyPair=$($cr.ec2KeyPair)"
    if ($ce.ecsConfiguration) { Write-Host "  ecsConfiguration cluster=$($ce.ecsConfiguration.cluster)" }
    $ceArn = $ce.computeEnvironmentArn
} else {
    Write-Host "CE describe failed or empty."
    $ceArn = $null
}

# --- 3) ASG(s) for this CE ---
Write-Host "`n--- 3) Auto Scaling Groups (Batch managed, tag aws:batch:computeEnvironmentArn) ---" -ForegroundColor Yellow
if ($ceArn) {
    $asgList = ExecJson @("autoscaling", "describe-auto-scaling-groups", "--region", $Region)
    $batchAsgs = @()
    if ($asgList -and $asgList.AutoScalingGroups) {
        foreach ($a in $asgList.AutoScalingGroups) {
            $tag = $a.Tags | Where-Object { $_.Key -eq "aws:batch:computeEnvironmentArn" } | Select-Object -First 1
            if ($tag -and $tag.Value -eq $ceArn) {
                $batchAsgs += $a
            }
        }
    }
    if ($batchAsgs.Count -eq 0) {
        Write-Host "No ASG found with tag aws:batch:computeEnvironmentArn=$ceArn"
    } else {
        foreach ($a in $batchAsgs) {
            Write-Host "  ASG $($a.AutoScalingGroupName) desired=$($a.DesiredCapacity) min=$($a.MinSize) max=$($a.MaxSize)"
            Write-Host "    status=$($a.Status) activities: $($a.ActivitiesCount)"
            if ($a.Activities -and $a.Activities.Count -gt 0) {
                $a.Activities | ForEach-Object { Write-Host "      activity: $($_.StatusCode) $($_.Cause)" }
            }
        }
    }
} else {
    Write-Host "Skip (no CE ARN)."
}

# --- 4) ECS cluster / container instances ---
Write-Host "`n--- 4) ECS cluster (Batch CE uses ECS backend) ---" -ForegroundColor Yellow
if ($ceDesc -and $ceDesc.computeEnvironments -and $ceDesc.computeEnvironments[0].ecsConfiguration) {
    $clusterName = $ceDesc.computeEnvironments[0].ecsConfiguration.cluster
    if ($clusterName) {
        $ecsClusters = ExecJson @("ecs", "describe-clusters", "--clusters", $clusterName, "--region", $Region)
        if ($ecsClusters -and $ecsClusters.clusters -and $ecsClusters.clusters.Count -gt 0) {
            $c = $ecsClusters.clusters[0]
            Write-Host "  cluster=$($c.clusterName) status=$($c.status) registeredContainerInstancesCount=$($c.registeredContainerInstancesCount) runningTasksCount=$($c.runningTasksCount)"
        }
        $instances = ExecJson @("ecs", "list-container-instances", "--cluster", $clusterName, "--region", $Region)
        if ($instances -and $instances.containerInstanceArns -and $instances.containerInstanceArns.Count -gt 0) {
            $descInst = ExecJson @("ecs", "describe-container-instances", "--cluster", $clusterName, "--container-instances", ($instances.containerInstanceArns -join ","), "--region", $Region)
            if ($descInst -and $descInst.containerInstances) {
                foreach ($i in $descInst.containerInstances) {
                    Write-Host "  instance $($i.ec2InstanceId) status=$($i.status) runningTasksCount=$($i.runningTasksCount)"
                }
            }
        } else {
            Write-Host "  No container instances registered in cluster."
        }
    } else {
        Write-Host "  CE has no ecsConfiguration.cluster."
    }
} else {
    Write-Host "  Skip (no CE or ecsConfiguration)."
}

# --- 5) Networking: subnets from CE ---
Write-Host "`n--- 5) Networking (subnet route / NAT hint) ---" -ForegroundColor Yellow
if ($ceDesc -and $ceDesc.computeEnvironments -and $ceDesc.computeEnvironments[0].computeResources.subnets) {
    $subnetIds = $ceDesc.computeEnvironments[0].computeResources.subnets
    foreach ($subId in $subnetIds) {
        $sub = ExecJson @("ec2", "describe-subnets", "--subnet-ids", $subId, "--region", $Region)
        if ($sub -and $sub.Subnets -and $sub.Subnets[0]) {
            $s = $sub.Subnets[0]
            $rtAssoc = ExecJson @("ec2", "describe-route-tables", "--filters", "association.subnet-id=$subId", "--region", $Region)
            $hasDefault = $false
            if ($rtAssoc -and $rtAssoc.RouteTables -and $rtAssoc.RouteTables.Count -gt 0) {
                foreach ($r in $rtAssoc.RouteTables[0].Routes) {
                    if ($r.DestinationCidrBlock -eq "0.0.0.0/0") {
                        $hasDefault = $true
                        $nat = ($r.GatewayId -match "nat-")
                        Write-Host "  subnet $subId ($($s.CidrBlock)) default route -> $($r.GatewayId) (nat=$nat)"
                    }
                }
            }
            if (-not $hasDefault) { Write-Host "  subnet $subId ($($s.CidrBlock)): no 0.0.0.0/0 route (instance may lack internet for ECR/CloudWatch)" }
        }
    }
} else {
    Write-Host "  No CE subnets."
}

# --- 6) EventBridge reconcile rule ---
Write-Host "`n--- 6) EventBridge rule (reconcile) ---" -ForegroundColor Yellow
$rule = ExecJson @("events", "describe-rule", "--name", "academy-reconcile-video-jobs", "--region", $Region)
if ($rule) {
    Write-Host "  State=$($rule.State) ScheduleExpression=$($rule.ScheduleExpression)"
} else {
    Write-Host "  Rule academy-reconcile-video-jobs not found or no access."
}

Write-Host "`n========== END DIAGNOSE ==========" -ForegroundColor Cyan
