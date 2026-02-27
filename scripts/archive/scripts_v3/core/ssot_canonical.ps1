# SSOT Canonical list — load env/prod.ps1 first. Used for delete-candidate decision + structural Drift comparison.
# No partial adoption or name absorption. Anything outside SSOT is DELETE CANDIDATE.
function Set-SSOTCanonicalLists {
    $script:SSOT_CE = @($script:VideoCEName, $script:OpsCEName)
    $script:SSOT_Queue = @($script:VideoQueueName, $script:OpsQueueName)
    $script:SSOT_JobDef = @($script:VideoJobDefName, $script:OpsJobDefReconcile, $script:OpsJobDefScanStuck, $script:OpsJobDefNetprobe)
    $script:SSOT_EventBridgeRule = @($script:EventBridgeReconcileRule, $script:EventBridgeScanStuckRule)
    $script:SSOT_ASG = @($script:MessagingASGName, $script:AiASGName)
    $script:SSOT_RDS = @($script:RdsDbIdentifier)
    $script:SSOT_Redis = @($script:RedisReplicationGroupId)
    $script:SSOT_ECR = @($script:EcrApiRepo, $script:VideoWorkerRepo, $script:EcrMessagingRepo, $script:EcrAiRepo)
    $script:SSOT_SSM = @($script:SsmApiEnv, $script:SsmWorkersEnv)
    $script:SSOT_EIP = @($script:ApiAllocationId)
    $script:SSOT_IAMRoles = @(
        "academy-batch-service-role",
        "academy-batch-ecs-instance-role",
        "academy-batch-ecs-task-execution-role",
        "academy-video-batch-job-role",
        "academy-eventbridge-batch-video-role"
    )
    $script:SSOT_InstanceProfile = @("academy-batch-ecs-instance-profile")
    $script:SSOT_ECSClusterPatterns = @("*academy-video-batch-ce-final*", "*academy-video-ops-ce*")

    # Canonical structure (for Drift comparison). Values from env + params.
    $script:SSOT_CE_Expected = @{
        $script:VideoCEName = @{
            instanceTypes = @("c6g.large")
            maxvCpus = 32
            subnets = $script:PublicSubnets
            securityGroupIds = @($script:BatchSecurityGroupId)
        }
        $script:OpsCEName = @{
            instanceTypes = @("c6g.large")
            maxvCpus = 2
            subnets = $script:PublicSubnets
            securityGroupIds = @($script:BatchSecurityGroupId)
        }
    }
    $script:SSOT_Queue_Priority = 1
    $script:SSOT_JobDef_Expected = @{
        $script:VideoJobDefName = @{ vcpus = 2; memory = 3072 }
        $script:OpsJobDefReconcile = @{ vcpus = 1; memory = 2048 }
        $script:OpsJobDefScanStuck = @{ vcpus = 1; memory = 2048 }
        $script:OpsJobDefNetprobe = @{ vcpus = 1; memory = 512 }
    }
}
