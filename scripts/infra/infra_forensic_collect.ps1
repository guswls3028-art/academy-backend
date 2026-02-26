# ==============================================================================
# AWS 인프라 포렌식 수집 — 재현 가능한 증거 기반. 추측 금지, CLI 출력만 사용.
# Usage: .\scripts\infra\infra_forensic_collect.ps1 -Region ap-northeast-2 [-OutDir "C:\academy\forensic_YYYYMMDD_HHmmss"]
# ==============================================================================
param(
    [string]$Region = "ap-northeast-2",
    [string]$OutDir = ""
)
$ErrorActionPreference = "Stop"
if (-not $OutDir) { $OutDir = Join-Path (Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))) "forensic_$(Get-Date -Format 'yyyyMMdd_HHmmss')" }
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
$OutDir = (Resolve-Path -LiteralPath $OutDir).Path

function Save-Json { param([string]$Name, [string]$Json) $path = Join-Path $OutDir "$Name.json"; [System.IO.File]::WriteAllText($path, $Json, [System.Text.UTF8Encoding]::new($false)) }
function Run-Aws { param([string]$Name, [string[]]$Args) $raw = & aws @Args 2>&1; $out = ($raw | Out-String).Trim(); if ($LASTEXITCODE -ne 0) { Save-Json $Name "{ `"Error`": `"ExitCode=$LASTEXITCODE`", `"Output`": $(($out -replace '\\','\\\\' -replace '"','\"') | ForEach-Object { "`"$_`"" }) }"; return $null }; return $out }

Write-Host "=== AWS 인프라 포렌식 수집 ===" -ForegroundColor Cyan
Write-Host "Region: $Region  OutDir: $OutDir" -ForegroundColor Gray

# Credential check
$identity = Run-Aws "01_caller_identity" @("sts", "get-caller-identity", "--output", "json")
if (-not $identity) { Write-Host "FAIL: AWS credentials invalid. Run aws sts get-caller-identity first." -ForegroundColor Red; exit 1 }
Save-Json "01_caller_identity" $identity
Write-Host "[1] Caller identity OK" -ForegroundColor Green

# 2) VPC
$vpcs = Run-Aws "02_vpcs" @("ec2", "describe-vpcs", "--region", $Region, "--output", "json"); if ($vpcs) { Save-Json "02_vpcs" $vpcs }
$subnets = Run-Aws "02_subnets" @("ec2", "describe-subnets", "--region", $Region, "--output", "json"); if ($subnets) { Save-Json "02_subnets" $subnets }
$routeTables = Run-Aws "02_route_tables" @("ec2", "describe-route-tables", "--region", $Region, "--output", "json"); if ($routeTables) { Save-Json "02_route_tables" $routeTables }
$natGateways = Run-Aws "02_nat_gateways" @("ec2", "describe-nat-gateways", "--region", $Region, "--output", "json"); if ($natGateways) { Save-Json "02_nat_gateways" $natGateways }
$igws = Run-Aws "02_internet_gateways" @("ec2", "describe-internet-gateways", "--region", $Region, "--output", "json"); if ($igws) { Save-Json "02_internet_gateways" $igws }
$vpcEndpoints = Run-Aws "02_vpc_endpoints" @("ec2", "describe-vpc-endpoints", "--region", $Region, "--output", "json"); if ($vpcEndpoints) { Save-Json "02_vpc_endpoints" $vpcEndpoints }
$sgs = Run-Aws "02_security_groups" @("ec2", "describe-security-groups", "--region", $Region, "--output", "json"); if ($sgs) { Save-Json "02_security_groups" $sgs }
Write-Host "[2] VPC/Subnet/Route/NAT/IGW/Endpoints/SG collected" -ForegroundColor Green

# 3) API EC2
$apiInstances = Run-Aws "03_api_instances" @("ec2", "describe-instances", "--region", $Region, "--filters", "Name=tag:Name", "Values=*api*", "Name=instance-state-name", "Values=running", "--output", "json"); if ($apiInstances) { Save-Json "03_api_instances" $apiInstances }
Write-Host "[3] API instances collected" -ForegroundColor Green

# 4) Build EC2
$buildInstances = Run-Aws "04_build_instances" @("ec2", "describe-instances", "--region", $Region, "--filters", "Name=tag:Name", "Values=academy-build-arm64", "Name=instance-state-name", "Values=running,stopped", "--output", "json"); if ($buildInstances) { Save-Json "04_build_instances" $buildInstances }
Write-Host "[4] Build instances collected" -ForegroundColor Green

# 5) Batch Video
$batchCE = Run-Aws "05_batch_compute_environments" @("batch", "describe-compute-environments", "--region", $Region, "--output", "json"); if ($batchCE) { Save-Json "05_batch_compute_environments" $batchCE }
$batchJQ = Run-Aws "05_batch_job_queues" @("batch", "describe-job-queues", "--region", $Region, "--output", "json"); if ($batchJQ) { Save-Json "05_batch_job_queues" $batchJQ }
$batchJD = Run-Aws "05_batch_job_definitions" @("batch", "describe-job-definitions", "--region", $Region, "--status", "ACTIVE", "--output", "json"); if ($batchJD) { Save-Json "05_batch_job_definitions" $batchJD }
Write-Host "[5] Batch CE/Queue/JobDef collected" -ForegroundColor Green

# 6) Batch Ops jobs
$opsRunnable = Run-Aws "06_ops_jobs_runnable" @("batch", "list-jobs", "--region", $Region, "--job-queue", "academy-video-ops-queue", "--job-status", "RUNNABLE", "--output", "json"); if ($opsRunnable) { Save-Json "06_ops_jobs_runnable" $opsRunnable }
$opsRunning = Run-Aws "06_ops_jobs_running" @("batch", "list-jobs", "--region", $Region, "--job-queue", "academy-video-ops-queue", "--job-status", "RUNNING", "--output", "json"); if ($opsRunning) { Save-Json "06_ops_jobs_running" $opsRunning }

# 7) EventBridge
$ruleReconcile = Run-Aws "07_eventbridge_reconcile" @("events", "describe-rule", "--name", "academy-reconcile-video-jobs", "--region", $Region, "--output", "json"); if ($ruleReconcile) { Save-Json "07_eventbridge_reconcile" $ruleReconcile }
$ruleScanStuck = Run-Aws "07_eventbridge_scanstuck" @("events", "describe-rule", "--name", "academy-video-scan-stuck-rate", "--region", $Region, "--output", "json"); if ($ruleScanStuck) { Save-Json "07_eventbridge_scanstuck" $ruleScanStuck }
$tgtReconcile = Run-Aws "07_eventbridge_reconcile_targets" @("events", "list-targets-by-rule", "--rule", "academy-reconcile-video-jobs", "--region", $Region, "--output", "json"); if ($tgtReconcile) { Save-Json "07_eventbridge_reconcile_targets" $tgtReconcile }
$tgtScanStuck = Run-Aws "07_eventbridge_scanstuck_targets" @("events", "list-targets-by-rule", "--rule", "academy-video-scan-stuck-rate", "--region", $Region, "--output", "json"); if ($tgtScanStuck) { Save-Json "07_eventbridge_scanstuck_targets" $tgtScanStuck }
Write-Host "[7] EventBridge rules/targets collected" -ForegroundColor Green

# 8) ECR
$ecrRepos = Run-Aws "08_ecr_repositories" @("ecr", "describe-repositories", "--region", $Region, "--output", "json"); if ($ecrRepos) { Save-Json "08_ecr_repositories" $ecrRepos }
$ecrVideoImages = Run-Aws "08_ecr_video_worker_images" @("ecr", "describe-images", "--repository-name", "academy-video-worker", "--region", $Region, "--output", "json"); if ($ecrVideoImages) { Save-Json "08_ecr_video_worker_images" $ecrVideoImages }
Write-Host "[8] ECR collected" -ForegroundColor Green

# 9) IAM (role names from SSOT)
$roleNames = @("academy-batch-service-role", "academy-batch-ecs-instance-role", "academy-video-batch-job-role", "academy-batch-ecs-task-execution-role")
foreach ($rn in $roleNames) {
    $safe = $rn -replace '[^a-zA-Z0-9]', '_'
    $roleOut = Run-Aws "09_iam_role_$safe" @("iam", "get-role", "--role-name", $rn, "--output", "json")
    if ($roleOut) { Save-Json "09_iam_role_$safe" $roleOut; $attached = Run-Aws "09_iam_role_${safe}_attached" @("iam", "list-attached-role-policies", "--role-name", $rn, "--output", "json"); if ($attached) { Save-Json "09_iam_role_${safe}_attached" $attached } }
}
Write-Host "[9] IAM roles collected" -ForegroundColor Green

# ECS/ASG/LaunchTemplate (from CE if present)
$ceObj = $null; if ($batchCE) { try { $ceObj = $batchCE | ConvertFrom-Json } catch {} }
if ($ceObj -and $ceObj.computeEnvironments) {
    $videoCE = $ceObj.computeEnvironments | Where-Object { $_.computeEnvironmentName -eq "academy-video-batch-ce-final" } | Select-Object -First 1
    if ($videoCE -and $videoCE.computeResources.autoScalingGroupArn) {
        $asgName = ($videoCE.computeResources.autoScalingGroupArn -split "/")[-1]
        $asgOut = Run-Aws "05_asg_video" @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $asgName, "--region", $Region, "--output", "json"); if ($asgOut) { Save-Json "05_asg_video" $asgOut }
        if ($videoCE.computeResources.ecsClusterArn) {
            $clusterName = ($videoCE.computeResources.ecsClusterArn -split "/")[-1]
            $ecsInstances = Run-Aws "05_ecs_container_instances" @("ecs", "list-container-instances", "--cluster", $clusterName, "--region", $Region, "--output", "json"); if ($ecsInstances) { Save-Json "05_ecs_container_instances" $ecsInstances }
        }
    }
}

# Build instance: route table for its subnet
if ($buildInstances) {
    try {
        $b = $buildInstances | ConvertFrom-Json
        $buildInst = $b.Reservations | ForEach-Object { $_.Instances } | Where-Object { $_.Tags | Where-Object { $_.Key -eq "Name" -and $_.Value -match "build" } } | Select-Object -First 1
        if ($buildInst -and $buildInst.SubnetId) {
            $rtAssoc = Run-Aws "04_build_subnet_route_tables" @("ec2", "describe-route-tables", "--region", $Region, "--filters", "Name=association.subnet-id", "Values=$($buildInst.SubnetId)", "--output", "json"); if ($rtAssoc) { Save-Json "04_build_subnet_route_tables" $rtAssoc }
        }
    } catch {}
}

# Report generator
$reportPath = Join-Path $OutDir "REPORT.md"
$sb = [System.Text.StringBuilder]::new()
[void]$sb.AppendLine("# AWS 인프라 포렌식 보고서")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("Region: $Region  |  수집 시각: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  |  OutDir: $OutDir")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("---")
[void]$sb.AppendLine("## 1. 네트워크 구조 요약")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("| 항목 | 증거 파일 |")
[void]$sb.AppendLine("|------|------------|")
[void]$sb.AppendLine("| VPC | 02_vpcs.json |")
[void]$sb.AppendLine("| Subnets | 02_subnets.json |")
[void]$sb.AppendLine("| Route Tables | 02_route_tables.json |")
[void]$sb.AppendLine("| NAT Gateways | 02_nat_gateways.json |")
[void]$sb.AppendLine("| Internet Gateways | 02_internet_gateways.json |")
[void]$sb.AppendLine("| VPC Endpoints | 02_vpc_endpoints.json |")
[void]$sb.AppendLine("| Security Groups | 02_security_groups.json |")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 2. 인터넷 경로 존재 여부 (API / Build / Worker)")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("- API: 03_api_instances.json → SubnetId → 02_route_tables.json / 02_nat_gateways.json 로 확인")
[void]$sb.AppendLine("- Build: 04_build_instances.json, 04_build_subnet_route_tables.json 로 확인")
[void]$sb.AppendLine("- Worker(Batch): 05_batch_compute_environments.json → subnets → 02_route_tables / 02_nat_gateways")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 3. SSOT 위반 체크 리스트")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("- Video CE: academy-video-batch-ce-final, state=ENABLED, status=VALID, instanceTypes=c6g.large 단일 → 05_batch_compute_environments.json")
[void]$sb.AppendLine("- Video Queue: CE 1개만 → 05_batch_job_queues.json")
[void]$sb.AppendLine("- JobDef: vcpus=2, memory=3072, retryStrategy.attempts=1 → 05_batch_job_definitions.json")
[void]$sb.AppendLine("- EventBridge reconcile: rate(15 minutes), target=Ops Queue → 07_eventbridge_*.json")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 4. 잠재적 장애 포인트")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("- Build 서버: 04_build_instances.json + 04_build_subnet_route_tables.json → 0.0.0.0/0 → nat/igw 없으면 STS/ECR 타임아웃")
[void]$sb.AppendLine("- Batch CE INVALID → 05_batch_compute_environments.json status/statusReason")
[void]$sb.AppendLine("- ECS Container Instances 0개 + desiredvCpus>0 → RUNNABLE 정체")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("## 5. 재구성 필요 여부")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("위 JSON 파일 기준으로 2~4 항목 검토 후 판단. 모든 증거는 동일 폴더 내 JSON 원문 참고.")
[void]$sb.AppendLine("")
[void]$sb.AppendLine("---")
[void]$sb.AppendLine("(모든 CLI 출력은 해당 디렉터리의 *.json 파일에 저장됨. 추측 없음.)")
[System.IO.File]::WriteAllText($reportPath, $sb.ToString(), [System.Text.UTF8Encoding]::new($false))

Write-Host "`nDone. Report: $reportPath" -ForegroundColor Green
Write-Host "All JSON evidence: $OutDir" -ForegroundColor Gray
