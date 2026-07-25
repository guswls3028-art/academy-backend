# Project cost-allocation tags for Academy resources.
$ErrorActionPreference = "Stop"

function Set-Ec2ProjectTags {
    param([string[]]$ResourceIds)
    $ids = @($ResourceIds | Where-Object { $_ } | Select-Object -Unique)
    if ($ids.Count -eq 0) { return }
    Invoke-Aws (
        @("ec2", "create-tags", "--resources") +
        $ids +
        @("--tags", "Key=Project,Value=academy", "Key=ManagedBy,Value=ssot-v1", "--region", $script:Region)
    ) -ErrorMessage "tag EC2 resources for Academy cost allocation" | Out-Null
}

function Ensure-ProjectCostAllocationTags {
    Write-Step "Ensure Project cost-allocation tags"
    if ($script:PlanMode) {
        $status = Invoke-AwsJson @(
            "ce", "list-cost-allocation-tags",
            "--tag-keys", "Project",
            "--region", "us-east-1",
            "--output", "json"
        )
        $current = if ($status.CostAllocationTags) { $status.CostAllocationTags[0].Status } else { "missing" }
        Write-Host "  Project cost-allocation tag: $current -> Active" -ForegroundColor Gray
        Write-Ok "Project resource tagging previewed (Plan)"
        return
    }

    $asgNames = @(
        $script:ApiASGName,
        $script:MessagingASGName,
        $script:AiASGName,
        $script:ToolsASGName
    ) | Where-Object { $_ }
    $asgRes = Invoke-AwsJson (
        @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names") +
        $asgNames +
        @("--region", $script:Region, "--output", "json")
    )
    $instanceIds = @()
    foreach ($asg in @($asgRes.AutoScalingGroups)) {
        $tagArgs = @(
            "ResourceId=$($asg.AutoScalingGroupName),ResourceType=auto-scaling-group,Key=Project,Value=academy,PropagateAtLaunch=true",
            "ResourceId=$($asg.AutoScalingGroupName),ResourceType=auto-scaling-group,Key=ManagedBy,Value=ssot-v1,PropagateAtLaunch=true"
        )
        Invoke-Aws @(
            "autoscaling", "create-or-update-tags",
            "--tags", $tagArgs[0], $tagArgs[1],
            "--region", $script:Region
        ) -ErrorMessage "tag ASG $($asg.AutoScalingGroupName)" | Out-Null
        $instanceIds += @($asg.Instances | ForEach-Object { $_.InstanceId } | Where-Object { $_ })
    }

    if ($instanceIds.Count -gt 0) {
        Set-Ec2ProjectTags -ResourceIds $instanceIds
        $volumeRes = Invoke-AwsJson (
            @("ec2", "describe-volumes", "--filters", "Name=attachment.instance-id,Values=$($instanceIds -join ',')") +
            @("--region", $script:Region, "--output", "json")
        )
        Set-Ec2ProjectTags -ResourceIds @($volumeRes.Volumes | ForEach-Object { $_.VolumeId })
    }

    if ($script:RdsDbIdentifier) {
        $rds = Invoke-AwsJson @(
            "rds", "describe-db-instances",
            "--db-instance-identifier", $script:RdsDbIdentifier,
            "--region", $script:Region,
            "--output", "json"
        )
        if ($rds.DBInstances) {
            Invoke-Aws @(
                "rds", "add-tags-to-resource",
                "--resource-name", $rds.DBInstances[0].DBInstanceArn,
                "--tags", "Key=Project,Value=academy", "Key=ManagedBy,Value=ssot-v1",
                "--region", $script:Region
            ) -ErrorMessage "tag RDS $($script:RdsDbIdentifier)" | Out-Null
        }
    }

    if ($script:RedisReplicationGroupId) {
        $redis = Invoke-AwsJson @(
            "elasticache", "describe-replication-groups",
            "--replication-group-id", $script:RedisReplicationGroupId,
            "--region", $script:Region,
            "--output", "json"
        )
        if ($redis.ReplicationGroups) {
            Invoke-Aws @(
                "elasticache", "add-tags-to-resource",
                "--resource-name", $redis.ReplicationGroups[0].ARN,
                "--tags", "Key=Project,Value=academy", "Key=ManagedBy,Value=ssot-v1",
                "--region", $script:Region
            ) -ErrorMessage "tag Redis $($script:RedisReplicationGroupId)" | Out-Null
        }
    }

    $queueNames = @(
        $script:MessagingSqsQueueName,
        "$($script:MessagingSqsQueueName)$($script:MessagingDlqSuffix)",
        $script:AiSqsQueueName,
        "$($script:AiSqsQueueName)$($script:AiDlqSuffix)",
        $script:ToolsSqsQueueName,
        "$($script:ToolsSqsQueueName)$($script:MessagingDlqSuffix)"
    ) | Where-Object { $_ } | Select-Object -Unique
    foreach ($queueName in $queueNames) {
        try {
            $queue = Invoke-AwsJson @(
                "sqs", "get-queue-url",
                "--queue-name", $queueName,
                "--region", $script:Region,
                "--output", "json"
            )
            if ($queue.QueueUrl) {
                Invoke-Aws @(
                    "sqs", "tag-queue",
                    "--queue-url", $queue.QueueUrl,
                    "--tags", "Project=academy,ManagedBy=ssot-v1",
                    "--region", $script:Region
                ) -ErrorMessage "tag SQS $queueName" | Out-Null
            }
        } catch {
            Write-Warn "Cost tag skipped for missing SQS queue: $queueName"
        }
    }

    if ($script:ApiAlbName) {
        $albs = Invoke-AwsJson @(
            "elbv2", "describe-load-balancers",
            "--names", $script:ApiAlbName,
            "--region", $script:Region,
            "--output", "json"
        )
        if ($albs.LoadBalancers) {
            Invoke-Aws @(
                "elbv2", "add-tags",
                "--resource-arns", $albs.LoadBalancers[0].LoadBalancerArn,
                "--tags", "Key=Project,Value=academy", "Key=ManagedBy,Value=ssot-v1",
                "--region", $script:Region
            ) -ErrorMessage "tag ALB $($script:ApiAlbName)" | Out-Null
        }
    }
    if ($script:ApiTargetGroupArn) {
        Invoke-Aws @(
            "elbv2", "add-tags",
            "--resource-arns", $script:ApiTargetGroupArn,
            "--tags", "Key=Project,Value=academy", "Key=ManagedBy,Value=ssot-v1",
            "--region", $script:Region
        ) -ErrorMessage "tag target group $($script:ApiTargetGroupName)" | Out-Null
    }

    $batchNames = @($script:VideoCEName, $script:OpsCEName) | Where-Object { $_ }
    if ($batchNames.Count -gt 0) {
        $batch = Invoke-AwsJson (
            @("batch", "describe-compute-environments", "--compute-environments") +
            $batchNames +
            @("--region", $script:Region, "--output", "json")
        )
        foreach ($ce in @($batch.computeEnvironments)) {
            Invoke-Aws @(
                "batch", "tag-resource",
                "--resource-arn", $ce.computeEnvironmentArn,
                "--tags", "Project=academy,ManagedBy=ssot-v1",
                "--region", $script:Region
            ) -ErrorMessage "tag Batch compute environment $($ce.computeEnvironmentName)" | Out-Null
        }
    }
    $batchQueueNames = @($script:VideoQueueName, $script:OpsQueueName) | Where-Object { $_ }
    if ($batchQueueNames.Count -gt 0) {
        $queues = Invoke-AwsJson (
            @("batch", "describe-job-queues", "--job-queues") +
            $batchQueueNames +
            @("--region", $script:Region, "--output", "json")
        )
        foreach ($queue in @($queues.jobQueues)) {
            Invoke-Aws @(
                "batch", "tag-resource",
                "--resource-arn", $queue.jobQueueArn,
                "--tags", "Project=academy,ManagedBy=ssot-v1",
                "--region", $script:Region
            ) -ErrorMessage "tag Batch queue $($queue.jobQueueName)" | Out-Null
        }
    }

    $repoNames = @(
        $script:EcrBaseRepo,
        $script:EcrApiRepo,
        $script:VideoWorkerRepo,
        $script:EcrMessagingRepo,
        $script:EcrAiRepo,
        $script:EcrToolsRepo
    ) | Where-Object { $_ } | Select-Object -Unique
    foreach ($repoName in $repoNames) {
        try {
            $repo = Invoke-AwsJson @(
                "ecr", "describe-repositories",
                "--repository-names", $repoName,
                "--region", $script:Region,
                "--output", "json"
            )
            if ($repo.repositories) {
                Invoke-Aws @(
                    "ecr", "tag-resource",
                    "--resource-arn", $repo.repositories[0].repositoryArn,
                    "--tags", "Key=Project,Value=academy", "Key=ManagedBy,Value=ssot-v1",
                    "--region", $script:Region
                ) -ErrorMessage "tag ECR $repoName" | Out-Null
            }
        } catch {
            Write-Warn "Cost tag skipped for missing ECR repository: $repoName"
        }
    }

    foreach ($tableName in @($script:DynamoLockTableName, $script:DynamoUploadCheckpointTableName) | Where-Object { $_ }) {
        try {
            $table = Invoke-AwsJson @(
                "dynamodb", "describe-table",
                "--table-name", $tableName,
                "--region", $script:Region,
                "--output", "json"
            )
            if ($table.Table.TableArn) {
                Invoke-Aws @(
                    "dynamodb", "tag-resource",
                    "--resource-arn", $table.Table.TableArn,
                    "--tags", "Key=Project,Value=academy", "Key=ManagedBy,Value=ssot-v1",
                    "--region", $script:Region
                ) -ErrorMessage "tag DynamoDB $tableName" | Out-Null
            }
        } catch {
            Write-Warn "Cost tag skipped for missing DynamoDB table: $tableName"
        }
    }

    $activation = '[{"TagKey":"Project","Status":"Active"}]'
    Invoke-Aws @(
        "ce", "update-cost-allocation-tags-status",
        "--cost-allocation-tags-status", $activation,
        "--region", "us-east-1"
    ) -ErrorMessage "activate Project cost-allocation tag" | Out-Null
    Write-Ok "Project=academy cost tags applied; Cost Explorer activation requested"
}
