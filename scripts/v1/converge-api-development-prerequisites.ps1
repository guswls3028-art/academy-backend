# One-time/idempotent bootstrap for the isolated persistent development API.
# This script does not launch an instance or touch production runtime capacity.
# Its only shared-network change is one exact PostgreSQL ingress rule from the
# no-inbound development SG to the existing data SG.
[CmdletBinding()]
param(
    [ValidateRange(60, 600)]
    [int]$DatabaseTimeoutSec = 300,
    [string]$AwsProfile = "default"
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
if ($AwsProfile -and $AwsProfile.Trim()) {
    $env:AWS_PROFILE = $AwsProfile.Trim()
}
if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" }

$script:PlanMode = $false
$script:ChangesMade = $false
. (Join-Path $ScriptRoot "core\env.ps1")
. (Join-Path $ScriptRoot "core\ssot.ps1")
. (Join-Path $ScriptRoot "core\logging.ps1")
. (Join-Path $ScriptRoot "core\aws.ps1")
. (Join-Path $ScriptRoot "resources\iam.ps1")
Assert-AwsMutationIdentity | Out-Null
Load-SSOT -Env prod | Out-Null

if (-not $script:ApiDevelopmentEnabled) {
    throw "Persistent API development environment is disabled in params.yaml."
}
if ($script:ApiDevelopmentAccessMode -ne "ssm-only") {
    throw "API development access must remain ssm-only."
}

function Ensure-DevelopmentQueue {
    param(
        [string]$QueueName,
        [int]$VisibilityTimeout = 1800
    )
    $dlqName = "${QueueName}-dlq"
    foreach ($name in @($dlqName, $QueueName)) {
        $found = Invoke-AwsJson @(
            "sqs", "get-queue-url",
            "--queue-name", $name,
            "--region", $script:Region,
            "--output", "json"
        )
        if (-not $found -or -not $found.QueueUrl) {
            $created = Invoke-AwsJson @(
                "sqs", "create-queue",
                "--queue-name", $name,
                "--tags", "Project=academy,Environment=development,ManagedBy=academy-api-development",
                "--region", $script:Region,
                "--output", "json"
            )
            if (-not $created -or -not $created.QueueUrl) {
                throw "Failed to create development queue: $name"
            }
        }
    }

    $queue = Invoke-AwsJson @(
        "sqs", "get-queue-url",
        "--queue-name", $QueueName,
        "--region", $script:Region,
        "--output", "json"
    )
    $dlq = Invoke-AwsJson @(
        "sqs", "get-queue-url",
        "--queue-name", $dlqName,
        "--region", $script:Region,
        "--output", "json"
    )
    $dlqAttributes = Invoke-AwsJson @(
        "sqs", "get-queue-attributes",
        "--queue-url", $dlq.QueueUrl,
        "--attribute-names", "QueueArn",
        "--region", $script:Region,
        "--output", "json"
    )
    $attributes = [ordered]@{
        VisibilityTimeout = [string]$VisibilityTimeout
        MessageRetentionPeriod = "345600"
        ReceiveMessageWaitTimeSeconds = "20"
        RedrivePolicy = (
            [ordered]@{
                deadLetterTargetArn = [string]$dlqAttributes.Attributes.QueueArn
                maxReceiveCount = "5"
            } | ConvertTo-Json -Compress
        )
    } | ConvertTo-Json -Compress
    $attributesRef = Convert-JsonArgToFileRef $attributes
    $attributesFile = $attributesRef -replace '^file://', ''
    try {
        Invoke-Aws @(
            "sqs", "set-queue-attributes",
            "--queue-url", $queue.QueueUrl,
            "--attributes", $attributesRef,
            "--region", $script:Region
        ) -ErrorMessage "set development queue attributes for $QueueName" | Out-Null
    } finally {
        Remove-TempFiles @($attributesFile)
    }
}

function Assert-DevelopmentR2CredentialParameter {
    $result = Invoke-AwsJson @(
        "ssm", "describe-parameters",
        "--parameter-filters",
        "Key=Name,Option=Equals,Values=$($script:ApiDevelopmentR2CredentialParameter)",
        "--region", $script:Region,
        "--output", "json"
    )
    $parameters = @($result.Parameters)
    if ($parameters.Count -ne 1 -or [string]$parameters[0].Type -ne "SecureString") {
        throw (
            "Dedicated development R2 credentials must already exist as SecureString: " +
            $script:ApiDevelopmentR2CredentialParameter
        )
    }
}

function Ensure-DevelopmentSecurityGroup {
    $result = Invoke-AwsJson @(
        "ec2", "describe-security-groups",
        "--filters",
        "Name=vpc-id,Values=$($script:VpcId)",
        "Name=group-name,Values=$($script:ApiDevelopmentSecurityGroupName)",
        "--region", $script:Region,
        "--output", "json"
    )
    $group = @($result.SecurityGroups)[0]
    if (-not $group) {
        $created = Invoke-AwsJson @(
            "ec2", "create-security-group",
            "--group-name", $script:ApiDevelopmentSecurityGroupName,
            "--description", "SSM-only persistent Academy API development runtime",
            "--vpc-id", $script:VpcId,
            "--tag-specifications",
            "ResourceType=security-group,Tags=[{Key=Project,Value=academy},{Key=Environment,Value=development},{Key=ManagedBy,Value=academy-api-development}]",
            "--region", $script:Region,
            "--output", "json"
        )
        if (-not $created -or -not $created.GroupId) {
            throw "Failed to create the API development security group."
        }
        $readback = Invoke-AwsJson @(
            "ec2", "describe-security-groups",
            "--group-ids", $created.GroupId,
            "--region", $script:Region,
            "--output", "json"
        )
        $group = @($readback.SecurityGroups)[0]
    }
    if (@($group.IpPermissions).Count -ne 0) {
        throw "API development security group must have no inbound rules."
    }
    if (-not $script:SecurityGroupData) {
        throw "The shared data security group is missing from network SSOT."
    }
    $dataGroupResult = Invoke-AwsJson @(
        "ec2", "describe-security-groups",
        "--group-ids", $script:SecurityGroupData,
        "--region", $script:Region,
        "--output", "json"
    )
    $dataGroup = @($dataGroupResult.SecurityGroups)[0]
    $hasPostgresIngress = @(
        $dataGroup.IpPermissions |
            Where-Object {
                $_.IpProtocol -eq "tcp" -and
                [int]$_.FromPort -eq 5432 -and
                [int]$_.ToPort -eq 5432 -and
                @($_.UserIdGroupPairs | Where-Object {
                    $_.GroupId -eq [string]$group.GroupId
                }).Count -gt 0
            }
    ).Count -gt 0
    if (-not $hasPostgresIngress) {
        Invoke-Aws @(
            "ec2", "authorize-security-group-ingress",
            "--group-id", $script:SecurityGroupData,
            "--protocol", "tcp",
            "--port", "5432",
            "--source-group", [string]$group.GroupId,
            "--region", $script:Region
        ) -ErrorMessage "authorize development PostgreSQL path" | Out-Null
        $dataGroupResult = Invoke-AwsJson @(
            "ec2", "describe-security-groups",
            "--group-ids", $script:SecurityGroupData,
            "--region", $script:Region,
            "--output", "json"
        )
        $dataGroup = @($dataGroupResult.SecurityGroups)[0]
        $hasPostgresIngress = @(
            $dataGroup.IpPermissions |
                Where-Object {
                    $_.IpProtocol -eq "tcp" -and
                    [int]$_.FromPort -eq 5432 -and
                    [int]$_.ToPort -eq 5432 -and
                    @($_.UserIdGroupPairs | Where-Object {
                        $_.GroupId -eq [string]$group.GroupId
                    }).Count -gt 0
                }
        ).Count -gt 0
    }
    if (-not $hasPostgresIngress) {
        throw "Development PostgreSQL source-SG rule readback failed."
    }
    Write-Ok "API development security group is SSM-only (no inbound rules)."
}

Assert-DevelopmentR2CredentialParameter
Ensure-DevelopmentSecurityGroup
Ensure-ApiDevelopmentIAM | Out-Null
Ensure-DevelopmentQueue -QueueName $script:ApiDevelopmentAiQueueName
Ensure-DevelopmentQueue -QueueName $script:ApiDevelopmentToolsQueueName
Ensure-DevelopmentQueue -QueueName $script:ApiDevelopmentMessagingQueueName -VisibilityTimeout 900

& (Join-Path $ScriptRoot "converge-api-development-database.ps1") `
    -TimeoutSec $DatabaseTimeoutSec `
    -AwsProfile $AwsProfile

Write-Ok (
    "API development prerequisites converged without production runtime mutation " +
    "(dedicated IAM, DB role/database, queues, and pre-provisioned development-only R2)."
)

