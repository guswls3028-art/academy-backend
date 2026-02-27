# ==============================================================================
# One-shot: 꼬인 Video CE 정리 후 단일 "final" CE로 Video 큐 고정 (SSOT lock)
#
# - 기존 v2/v3/public CE는 DISABLED만 (desiredvCpus=0은 큐 연결 시 API 불가)
# - VideoQ/OpsQ의 SUBMITTED~RUNNING job 일괄 cancel/terminate
# - 기존 CE에서 VPC/Subnets/SG/역할 읽어 재사용
# - Public subnet 강화 (IGW, 0.0.0.0/0, MapPublicIpOnLaunch, SG egress)
# - academy-video-batch-ce-final 생성 후 Video 큐를 이 CE만 쓰도록 고정
#
# Usage: .\scripts\infra\one_shot_video_ce_final.ps1
#        (또는 $Region 등 상단 변수 수정 후 실행)
# ==============================================================================

$ErrorActionPreference = "Stop"
$Region = "ap-northeast-2"

# SSOT 이름 (필요하면 여기만 바꿔)
$VideoQ  = "academy-video-batch-queue"
$OpsQ    = "academy-video-ops-queue"

# 지금까지 꼬인 CE들 (있으면 정지)
$OldVideoCEs = @("academy-video-batch-ce-v2","academy-video-batch-ce-v3","academy-video-batch-ce-public")

# 최종으로 남길 "단일" Video CE 이름
$FinalVideoCE = "academy-video-batch-ce-final"

Write-Host "=== 0) Safety: stop cost bleed (disable schedulers optional) ==="
# 스케줄러는 네가 이미 꺼둔 게 많지만, 혹시 몰라서 enable 상태만 유지하고 싶으면 주석 처리
# aws events disable-rule --name academy-reconcile-video-jobs --region $Region | Out-Null
# aws events disable-rule --name academy-video-scan-stuck-rate --region $Region | Out-Null
# aws events disable-rule --name academy-worker-queue-depth-rate --region $Region | Out-Null

Write-Host "=== 1) Freeze all old Video CEs (DISABLED only; desiredvCpus=0 not supported while queue attached) ==="
foreach ($ceName in $OldVideoCEs) {
  $prevErr = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    aws batch update-compute-environment --compute-environment $ceName --state DISABLED --region $Region 2>&1 | Out-Null
  } finally {
    $ErrorActionPreference = $prevErr
  }
}

Write-Host "=== 2) Cancel/Terminate any jobs in VideoQ/OpsQ (leave SUCCEEDED/FAILED alone) ==="
function Clear-BatchQueueJobs {
  param([string]$QueueName,[string]$Region)
  $cancelStatuses = @("SUBMITTED","PENDING","RUNNABLE","STARTING")
  foreach ($st in $cancelStatuses) {
    $ids = aws batch list-jobs --job-queue $QueueName --job-status $st --region $Region --query "jobSummaryList[].jobId" --output text
    if ($ids) {
      foreach ($id in $ids -split "\s+") {
        if ($id) { aws batch cancel-job --job-id $id --reason "one-shot rebuild cleanup" --region $Region | Out-Null }
      }
    }
  }
  $runIds = aws batch list-jobs --job-queue $QueueName --job-status "RUNNING" --region $Region --query "jobSummaryList[].jobId" --output text
  if ($runIds) {
    foreach ($id in $runIds -split "\s+") {
      if ($id) { aws batch terminate-job --job-id $id --reason "one-shot rebuild cleanup" --region $Region | Out-Null }
    }
  }
}
Clear-BatchQueueJobs -QueueName $VideoQ -Region $Region
Clear-BatchQueueJobs -QueueName $OpsQ   -Region $Region

Write-Host "=== 3) Read VPC/Subnets/SG/InstanceProfile/ServiceRole from an existing (known) video CE if present ==="
# 기준 CE: v2가 있으면 v2에서 읽고, 없으면 public에서 읽음
$seedCE = $null
foreach ($cand in @("academy-video-batch-ce-v2","academy-video-batch-ce-public","academy-video-batch-ce-v3")) {
  try {
    $tmp = (aws batch describe-compute-environments --compute-environments $cand --region $Region | ConvertFrom-Json).computeEnvironments[0]
    if ($tmp) { $seedCE = $tmp; break }
  } catch {}
}
if (-not $seedCE) { throw "No seed compute environment found to reuse VPC/Subnets/SG. (expected v2/public/v3 to exist)" }

$subnets = $seedCE.computeResources.subnets
$sgIds   = $seedCE.computeResources.securityGroupIds
$sgsCsv  = ($sgIds) -join ","
$instanceRole = $seedCE.computeResources.instanceRole
$serviceRole  = $seedCE.serviceRole

$vpcId = (aws ec2 describe-subnets --subnet-ids $subnets[0] --region $Region --query "Subnets[0].VpcId" --output text)

Write-Host "=== 4) Public subnet hardening: ensure IGW attached + MAIN route table has 0.0.0.0/0 + MapPublicIpOnLaunch ==="
# 4-1) IGW ensure
$igwId = aws ec2 describe-internet-gateways --region $Region `
  --filters Name=attachment.vpc-id,Values=$vpcId `
  --query "InternetGateways[0].InternetGatewayId" --output text

if ($igwId -eq "None" -or !$igwId) {
  $igwId = aws ec2 create-internet-gateway --region $Region --query "InternetGateway.InternetGatewayId" --output text
  aws ec2 attach-internet-gateway --internet-gateway-id $igwId --vpc-id $vpcId --region $Region | Out-Null
}

# 4-2) MAIN route table default route ensure (중요: association.subnet-id로는 main이 안 잡히는 경우가 있어서 main을 강제로 본다)
$mainRtbId = aws ec2 describe-route-tables --region $Region `
  --filters Name=vpc-id,Values=$vpcId Name=association.main,Values=true `
  --query "RouteTables[0].RouteTableId" --output text

if ($mainRtbId -ne "None" -and $mainRtbId) {
  $hasDefault = aws ec2 describe-route-tables --route-table-ids $mainRtbId --region $Region `
    --query "RouteTables[0].Routes[?DestinationCidrBlock=='0.0.0.0/0'] | length(@)" --output text
  if ($hasDefault -eq "0") {
    aws ec2 create-route --route-table-id $mainRtbId --destination-cidr-block 0.0.0.0/0 --gateway-id $igwId --region $Region | Out-Null
  }
}

# 4-3) 각 subnet에 Public IP 자동할당 ON
foreach ($sn in $subnets) {
  aws ec2 modify-subnet-attribute --subnet-id $sn --map-public-ip-on-launch --region $Region | Out-Null
}

# 4-4) SG outbound 0.0.0.0/0 확인 (없으면 추가; 이미 있으면 Duplicate 에러 무시)
foreach ($sg in $sgIds) {
  $prevErr = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  aws ec2 authorize-security-group-egress --group-id $sg --ip-permissions IpProtocol=-1,IpRanges="[{CidrIp=0.0.0.0/0}]" --region $Region 2>&1 | Out-Null
  $ErrorActionPreference = $prevErr
}

Write-Host "=== 5) Ensure IAM essentials (Batch service role + Instance role policies) ==="
# Batch service role policy (이미 붙어있어도 OK)
try {
  aws iam attach-role-policy --role-name "academy-batch-service-role" --policy-arn "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole" | Out-Null
} catch {}

# Instance role essentials (SSM은 디버깅/운영에 도움)
try { aws iam attach-role-policy --role-name "academy-batch-ecs-instance-role" --policy-arn "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role" | Out-Null } catch {}
try { aws iam attach-role-policy --role-name "academy-batch-ecs-instance-role" --policy-arn "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly" | Out-Null } catch {}
try { aws iam attach-role-policy --role-name "academy-batch-ecs-instance-role" --policy-arn "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore" | Out-Null } catch {}

Write-Host "=== 6) Create FINAL single Video CE (if exists, reuse) ==="
$finalExists = $false
try {
  $x = (aws batch describe-compute-environments --compute-environments $FinalVideoCE --region $Region | ConvertFrom-Json).computeEnvironments[0]
  if ($x) { $finalExists = $true }
} catch {}

if (-not $finalExists) {
  aws batch create-compute-environment `
    --compute-environment-name $FinalVideoCE `
    --type MANAGED `
    --state ENABLED `
    --service-role $serviceRole `
    --compute-resources type=EC2,allocationStrategy=BEST_FIT_PROGRESSIVE,minvCpus=0,maxvCpus=32,desiredvCpus=0,instanceTypes=c6g.large,subnets=$($subnets -join ","),securityGroupIds=$sgsCsv,instanceRole=$instanceRole,ec2Configuration="[{imageType=ECS_AL2023}]" `
    --region $Region | Out-Null
}

# Wait VALID
for ($i=0; $i -lt 80; $i++) {
  $st = aws batch describe-compute-environments --compute-environments $FinalVideoCE --region $Region --query "computeEnvironments[0].status" --output text
  if ($st -eq "VALID") { break }
  Start-Sleep -Seconds 3
}
$st = aws batch describe-compute-environments --compute-environments $FinalVideoCE --region $Region --query "computeEnvironments[0].status" --output text
if ($st -ne "VALID") { throw "Final CE not VALID. status=$st" }

Write-Host "=== 7) Switch Video Queue to FINAL CE ONLY (SSOT lock) ==="
$finalArn = aws batch describe-compute-environments --compute-environments $FinalVideoCE --region $Region --query "computeEnvironments[0].computeEnvironmentArn" --output text
aws batch update-job-queue `
  --job-queue $VideoQ `
  --state ENABLED `
  --priority 1 `
  --compute-environment-order "order=1,computeEnvironment=$finalArn" `
  --region $Region | Out-Null

Write-Host "=== 8) Evidence dump (SSOT) ==="
Write-Host "--- Queue -> CE ---"
aws batch describe-job-queues --job-queues $VideoQ --region $Region --query "jobQueues[0].computeEnvironmentOrder[].computeEnvironment" --output table

Write-Host "--- Final CE ---"
aws batch describe-compute-environments --compute-environments $FinalVideoCE --region $Region `
  --query "computeEnvironments[0].{name:computeEnvironmentName,state:state,status:status,desired:computeResources.desiredvCpus,min:computeResources.minvCpus,max:computeResources.maxvCpus,instanceTypes:computeResources.instanceTypes,imageType:computeResources.ec2Configuration[0].imageType,subnets:computeResources.subnets,sgs:computeResources.securityGroupIds}" `
  --output json

Write-Host "--- ECS container instances (should become >0 after a job arrives) ---"
$clusterArn = aws batch describe-compute-environments --compute-environments $FinalVideoCE --region $Region --query "computeEnvironments[0].ecsClusterArn" --output text
$clusterName = ($clusterArn -split "/")[-1]
aws ecs list-container-instances --cluster $clusterName --region $Region --output table

Write-Host "--- Job counts in VideoQ ---"
$statuses=@("SUBMITTED","PENDING","RUNNABLE","STARTING","RUNNING","FAILED","SUCCEEDED")
foreach ($s in $statuses) {
  $c = aws batch list-jobs --job-queue $VideoQ --job-status $s --region $Region --query "length(jobSummaryList)" --output text
  Write-Host ("  {0,-9} {1}" -f $s, $c)
}

Write-Host "=== DONE: SSOT locked. Now upload 1 video again to trigger scaling on FINAL CE. ==="
