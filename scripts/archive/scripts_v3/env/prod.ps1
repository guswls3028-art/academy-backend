# SSOT v3 env — prod. Keep in sync with docs/00-SSOT/INFRA-SSOT-V3.params.yaml
$ErrorActionPreference = "Stop"
$script:Region = "ap-northeast-2"
$script:AccountId = "809466760795"
$script:VpcId = "vpc-0831a2484f9b114c2"
$script:PublicSubnets = @("subnet-049e711f41fdff71b", "subnet-07a8427d3306ce910", "subnet-09231ed7ecf59cfa4", "subnet-0548571ac21b3bbf3")
$script:BatchSecurityGroupId = "sg-011ed1d9eb4a65b8f"

$script:ApiAllocationId = "eipalloc-071ef2b5b5bec9428"
$script:ApiPublicIp = "15.165.147.157"
$script:ApiContainerName = "academy-api"
$script:ApiBaseUrl = "http://15.165.147.157:8000"

$script:VideoCEName = "academy-video-batch-ce-final"
$script:VideoQueueName = "academy-video-batch-queue"
$script:VideoJobDefName = "academy-video-batch-jobdef"
$script:OpsCEName = "academy-video-ops-ce"
$script:OpsQueueName = "academy-video-ops-queue"
$script:OpsJobDefReconcile = "academy-video-ops-reconcile"
$script:OpsJobDefScanStuck = "academy-video-ops-scanstuck"
$script:OpsJobDefNetprobe = "academy-video-ops-netprobe"

$script:EventBridgeReconcileRule = "academy-reconcile-video-jobs"
$script:EventBridgeScanStuckRule = "academy-video-scan-stuck-rate"
$script:EventBridgeRoleName = "academy-eventbridge-batch-video-role"

$script:MessagingASGName = "academy-messaging-worker-asg"
$script:AiASGName = "academy-ai-worker-asg"

$script:SsmWorkersEnv = "/academy/workers/env"
$script:SsmApiEnv = "/academy/api/env"

$script:VideoWorkerRepo = "academy-video-worker"
$script:VideoLogGroup = "/aws/batch/academy-video-worker"
$script:OpsLogGroup = "/aws/batch/academy-video-ops"

# FullStack SSOT (params.yaml sync)
$script:RdsDbIdentifier = "academy-db"
$script:RedisReplicationGroupId = "academy-redis"
$script:RedisSubnetGroupName = "academy-redis-subnets"
$script:RedisSecurityGroupId = "academy-redis-sg"
$script:BuildTagKey = "Name"
$script:BuildTagValue = "academy-build-arm64"
$script:ApiInstanceTagKey = "Name"
$script:ApiInstanceTagValue = "academy-api"
$script:EcrApiRepo = "academy-api"
$script:EcrMessagingRepo = "academy-messaging-worker"
$script:EcrAiRepo = "academy-ai-worker-cpu"
$script:MessagingLaunchTemplateName = "academy-messaging-worker-lt"
$script:AiLaunchTemplateName = "academy-ai-worker-lt"
