# SSOT v3 — prod 환경 변수 (INFRA-SSOT-V3.params.yaml과 동기화)
$script:Region = "ap-northeast-2"
$script:AccountId = "809466760795"
$script:VpcId = "vpc-0831a2484f9b114c2"
$script:SubnetIds = @(
    "subnet-049e711f41fdff71b",
    "subnet-07a8427d3306ce910",
    "subnet-09231ed7ecf59cfa4",
    "subnet-0548571ac21b3bbf3"
)
$script:SecurityGroupId = "sg-011ed1d9eb4a65b8f"

# Video Batch
$script:VideoCEName = "academy-video-batch-ce-final"
$script:VideoQueueName = "academy-video-batch-queue"
$script:VideoJobDefName = "academy-video-batch-jobdef"

# Ops Batch
$script:OpsCEName = "academy-video-ops-ce"
$script:OpsQueueName = "academy-video-ops-queue"
$script:OpsReconcileJobDef = "academy-video-ops-reconcile"
$script:OpsScanStuckJobDef = "academy-video-ops-scanstuck"
$script:OpsNetprobeJobDef = "academy-video-ops-netprobe"

# EventBridge
$script:ReconcileRuleName = "academy-reconcile-video-jobs"
$script:ScanStuckRuleName = "academy-video-scan-stuck-rate"

# API (Elastic IP)
$script:ApiEipAllocationId = "eipalloc-071ef2b5b5bec9428"
$script:ApiPublicIp = "15.165.147.157"
$script:ApiContainerName = "academy-api"

# ASG
$script:MessagingASGName = "academy-messaging-worker-asg"
$script:AiASGName = "academy-ai-worker-asg"

# SSM
$script:SsmWorkersEnv = "/academy/workers/env"
$script:SsmApiEnv = "/academy/api/env"
