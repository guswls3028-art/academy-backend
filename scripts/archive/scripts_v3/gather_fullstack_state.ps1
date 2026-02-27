# ==============================================================================
# FullStack current state gather — no code changes, Describe/List only, output report.
# Output: docs/00-SSOT/FULLSTACK-CURRENT-STATE-REPORT.md
# Usage: .\scripts_v3\gather_fullstack_state.ps1 [-Region ap-northeast-2] [-OutDir docs/00-SSOT]
# ==============================================================================
[CmdletBinding()]
param(
    [string]$Region = "ap-northeast-2",
    [string]$OutDir = ""
)
$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..")).Path
if (-not $OutDir) { $OutDir = Join-Path $RepoRoot "docs\00-SSOT" }
. (Join-Path $ScriptRoot "core\aws-wrapper.ps1")
. (Join-Path $ScriptRoot "core\logging.ps1")
. (Join-Path $ScriptRoot "env\prod.ps1")
$script:Region = $Region

$R = $Region
$sb = [System.Text.StringBuilder]::new()
function Out-Md { param([string]$Line) [void]$sb.AppendLine($Line) }

Out-Md "# Academy FullStack Current State Report"
Out-Md ""
Out-Md "**Generated:** $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') | **Region:** $R | **Read-only (no changes)**"
Out-Md ""

# --- Batch ---
Out-Md "## 1. Batch"
Out-Md ""
$ces = Invoke-AwsJson @("batch", "describe-compute-environments", "--region", $R, "--output", "json")
if ($ces -and $ces.computeEnvironments) {
    Out-Md "| CE Name | status | state | type | maxvCpus | instanceTypes |"
    Out-Md "|---------|--------|-------|------|----------|---------------|"
    foreach ($c in $ces.computeEnvironments) {
        $inst = ""; $maxV = ""
        if ($c.computeResources) { $inst = ($c.computeResources.instanceTypes -join ","); $maxV = $c.computeResources.maxvCpus }
        Out-Md "| $($c.computeEnvironmentName) | $($c.status) | $($c.state) | $($c.computeResources.type) | $maxV | $inst |"
    }
} else { Out-Md "No CE or describe failed" }
Out-Md ""
$queues = Invoke-AwsJson @("batch", "describe-job-queues", "--region", $R, "--output", "json")
if ($queues -and $queues.jobQueues) {
    Out-Md "| Queue Name | state | computeEnvironmentOrder |"
    Out-Md "|------------|-------|------------------------|"
    foreach ($q in $queues.jobQueues) {
        $order = ($q.computeEnvironmentOrder | ForEach-Object { $_.computeEnvironment }) -join "; "
        Out-Md "| $($q.jobQueueName) | $($q.state) | $order |"
    }
} else { Out-Md "No Queue" }
Out-Md ""
$jobDefList = Invoke-AwsJson @("batch", "describe-job-definitions", "--status", "ACTIVE", "--region", $R, "--output", "json")
if ($jobDefList -and $jobDefList.jobDefinitions) {
    $names = $jobDefList.jobDefinitions | Group-Object -Property jobDefinitionName | ForEach-Object { $_.Name } | Sort-Object -Unique
    Out-Md "| JobDef Name | latest revision | image | vcpus | memory |"
    Out-Md "|-------------|----------------|-------|-------|--------|"
    foreach ($n in $names) {
        $latest = $jobDefList.jobDefinitions | Where-Object { $_.jobDefinitionName -eq $n } | Sort-Object -Property revision -Descending | Select-Object -First 1
        if ($latest) {
            $img = $latest.containerProperties.image; $vc = $latest.containerProperties.vcpus; $mem = $latest.containerProperties.memory
            Out-Md "| $n | $($latest.revision) | $img | $vc | $mem |"
        }
    }
}
$jobs = Invoke-AwsJson @("batch", "list-jobs", "--job-queue", "academy-video-ops-queue", "--job-status", "RUNNABLE", "--region", $R, "--output", "json")
$runnableCount = if ($jobs -and $jobs.jobSummaryList) { $jobs.jobSummaryList.Count } else { 0 }
Out-Md ""
Out-Md "Ops Queue RUNNABLE job count: $runnableCount"
Out-Md ""

# --- EventBridge ---
Out-Md "## 2. EventBridge"
Out-Md ""
$rules = Invoke-AwsJson @("events", "list-rules", "--region", $R, "--output", "json")
if ($rules -and $rules.Rules) {
    Out-Md "| Rule Name | State | Schedule |"
    Out-Md "|-----------|-------|----------|"
    foreach ($r in $rules.Rules) { Out-Md "| $($r.Name) | $($r.State) | $($r.ScheduleExpression) |" }
    foreach ($r in $rules.Rules) {
        $tar = Invoke-AwsJson @("events", "list-targets-by-rule", "--rule", $r.Name, "--region", $R, "--output", "json")
        if ($tar -and $tar.Targets -and $tar.Targets.Count -gt 0) {
            Out-Md ""
            Out-Md "**Targets for $($r.Name):**"
            foreach ($t in $tar.Targets) {
                $jd = if ($t.BatchParameters -and $t.BatchParameters.JobDefinition) { $t.BatchParameters.JobDefinition } else { "-" }
                Out-Md "- Arn=$($t.Arn) JobDefinition=$jd"
            }
        }
    }
} else { Out-Md "No rules" }
Out-Md ""

# --- ASG ---
Out-Md "## 3. ASG (Messaging + AI)"
Out-Md ""
$asgNames = @("academy-messaging-worker-asg", "academy-ai-worker-asg")
foreach ($asgName in $asgNames) {
    $a = Invoke-AwsJson @("autoscaling", "describe-auto-scaling-groups", "--auto-scaling-group-names", $asgName, "--region", $R, "--output", "json")
    if ($a -and $a.AutoScalingGroups -and $a.AutoScalingGroups.Count -gt 0) {
        $x = $a.AutoScalingGroups[0]
        $lt = $x.LaunchTemplate; $ltVer = if ($lt) { $lt.Version } else { "-" }
        Out-Md "**$asgName**"
        Out-Md "- Desired=$($x.DesiredCapacity) Min=$($x.MinSize) Max=$($x.MaxSize) LaunchTemplate=$ltVer"
        $policies = Invoke-AwsJson @("autoscaling", "describe-policies", "--auto-scaling-group-name", $asgName, "--region", $R, "--output", "json")
        if ($policies -and $policies.ScalingPolicies) {
            foreach ($p in $policies.ScalingPolicies) { Out-Md "- Policy: $($p.PolicyName) Type=$($p.PolicyType)" }
        }
        Out-Md ""
    } else { Out-Md "**$asgName**: not found" ; Out-Md "" }
}
$ltList = Invoke-AwsJson @("ec2", "describe-launch-templates", "--region", $R, "--output", "json")
$academyLts = if ($ltList -and $ltList.LaunchTemplates) { $ltList.LaunchTemplates | Where-Object { $_.LaunchTemplateName -like "academy-*" } } else { @() }
Out-Md "| Launch Template | DefaultVersion |"
Out-Md "|-----------------|----------------|"
foreach ($lt in $academyLts) { Out-Md "| $($lt.LaunchTemplateName) | $($lt.DefaultVersionNumber) |" }
Out-Md ""

# --- EC2 API + Build ---
Out-Md "## 4. EC2 (API + Build)"
Out-Md ""
$eip = Invoke-AwsJson @("ec2", "describe-addresses", "--allocation-ids", "eipalloc-071ef2b5b5bec9428", "--region", $R, "--output", "json")
if ($eip -and $eip.Addresses -and $eip.Addresses.Count -gt 0 -and $eip.Addresses[0].InstanceId) {
    $apiInstId = $eip.Addresses[0].InstanceId
    $inst = Invoke-AwsJson @("ec2", "describe-instances", "--instance-ids", $apiInstId, "--region", $R, "--output", "json")
    $apiInst = $null
    if ($inst -and $inst.Reservations -and $inst.Reservations.Count -gt 0) {
        $apiInst = $inst.Reservations[0].Instances | Where-Object { $_.InstanceId -eq $apiInstId } | Select-Object -First 1
    }
    if ($apiInst) {
        $tags = ($apiInst.Tags | Where-Object { $_.Key -eq "Name" }).Value
        Out-Md "**API (EIP 15.165.147.157)**"
        Out-Md "- InstanceId=$apiInstId State=$($apiInst.State.Name) Name=$tags"
        Out-Md "- IamInstanceProfile: $($apiInst.IamInstanceProfile.Arn)"
        Out-Md "- SecurityGroups: $(($apiInst.SecurityGroups | ForEach-Object { $_.GroupId }) -join ', ')"
    }
} else { Out-Md "**API**: No instance attached to EIP" }
Out-Md ""
$buildInst = Invoke-AwsJson @("ec2", "describe-instances", "--filters", "Name=tag:Name,Values=academy-build-arm64", "Name=instance-state-name,Values=running,pending,stopped", "--region", $R, "--output", "json")
$bi = $null
if ($buildInst -and $buildInst.Reservations -and $buildInst.Reservations.Count -gt 0) {
    $bi = $buildInst.Reservations[0].Instances | Select-Object -First 1
}
if ($bi) {
    Out-Md "**Build (Tag Name=academy-build-arm64)**"
    Out-Md "- InstanceId=$($bi.InstanceId) State=$($bi.State.Name)"
    Out-Md "- IamInstanceProfile: $($bi.IamInstanceProfile.Arn)"
} else { Out-Md "**Build**: No academy-build-arm64 instance" }
Out-Md ""

# --- RDS ---
Out-Md "## 5. RDS"
Out-Md ""
$rds = Invoke-AwsJson @("rds", "describe-db-instances", "--region", $R, "--output", "json")
$db = $rds.DBInstances | Where-Object { $_.DBInstanceIdentifier -eq "academy-db" } | Select-Object -First 1
if ($db) {
    Out-Md "| Identifier | Status | Endpoint | Port | VpcSecurityGroups |"
    Out-Md "|------------|--------|----------|------|-------------------|"
    $sgs = ($db.VpcSecurityGroups | ForEach-Object { $_.VpcSecurityGroupId }) -join "; "
    Out-Md "| $($db.DBInstanceIdentifier) | $($db.DBInstanceStatus) | $($db.Endpoint.Address) | $($db.Endpoint.Port) | $sgs |"
} else { Out-Md "academy-db not found" }
Out-Md ""

# --- Redis ---
Out-Md "## 6. Redis (ElastiCache)"
Out-Md ""
$redis = Invoke-AwsJson @("elasticache", "describe-replication-groups", "--replication-group-id", "academy-redis", "--region", $R, "--output", "json")
if ($redis -and $redis.ReplicationGroups -and $redis.ReplicationGroups.Count -gt 0) {
    $rg = $redis.ReplicationGroups[0]
    $ep = $rg.NodeGroups[0].PrimaryEndpoint
    Out-Md "| ReplicationGroupId | Status | PrimaryEndpoint | Port | SecurityGroups |"
    Out-Md "|--------------------|--------|-----------------|------|----------------|"
    $sgIds = ($rg.SecurityGroups | ForEach-Object { $_.SecurityGroupId }) -join "; "
    Out-Md "| $($rg.ReplicationGroupId) | $($rg.Status) | $($ep.Address) | $($ep.Port) | $sgIds |"
} else { Out-Md "academy-redis not found" }
Out-Md ""

# --- SSM ---
Out-Md "## 7. SSM Parameters"
Out-Md ""
$ssmNames = @("/academy/api/env", "/academy/workers/env")
Out-Md "| Name | Exists |"
Out-Md "|------|--------|"
foreach ($nm in $ssmNames) {
    $exists = "no"
    try {
        $p = Invoke-AwsJson @("ssm", "get-parameter", "--name", $nm, "--region", $R, "--output", "json")
        if ($p -and $p.Parameter) { $exists = "yes (Type=$($p.Parameter.Type))" }
    } catch { }
    Out-Md "| $nm | $exists |"
}
Out-Md ""

# --- IAM ---
Out-Md "## 8. IAM (Academy roles)"
Out-Md ""
$roles = Invoke-AwsJson @("iam", "list-roles", "--output", "json")
$academyRoles = $roles.Roles | Where-Object { $_.RoleName -like "academy-*" } | Select-Object -ExpandProperty RoleName
Out-Md "| RoleName |"
Out-Md "|----------|"
foreach ($rn in ($academyRoles | Sort-Object)) { Out-Md "| $rn |" }
Out-Md ""

# --- ECR ---
Out-Md "## 9. ECR"
Out-Md ""
$ecrRepos = @("academy-api", "academy-video-worker", "academy-messaging-worker", "academy-ai-worker-cpu")
Out-Md "| Repository | Exists | Image tags (recent) |"
Out-Md "|-------------|--------|---------------------|"
foreach ($repo in $ecrRepos) {
    $exist = "no"; $tags = "-"
    try {
        $di = Invoke-AwsJson @("ecr", "describe-repositories", "--repository-names", $repo, "--region", $R, "--output", "json")
        if ($di -and $di.repositories) {
            $exist = "yes"
            $imgs = Invoke-AwsJson @("ecr", "list-images", "--repository-name", $repo, "--region", $R, "--max-items", "5", "--output", "json")
            if ($imgs -and $imgs.imageIds) { $tags = ($imgs.imageIds | ForEach-Object { $_.imageTag }) -join ", " }
        }
    } catch { }
    Out-Md "| $repo | $exist | $tags |"
}
Out-Md ""

# --- Network ---
Out-Md "## 10. Network (VPC/Subnets/Route)"
Out-Md ""
$vpcId = "vpc-0831a2484f9b114c2"
$vpc = Invoke-AwsJson @("ec2", "describe-vpcs", "--vpc-ids", $vpcId, "--region", $R, "--output", "json")
if ($vpc -and $vpc.Vpcs) {
    Out-Md "| VpcId | CidrBlock |"
    Out-Md "|-------|-----------|"
    Out-Md "| $($vpc.Vpcs[0].VpcId) | $($vpc.Vpcs[0].CidrBlock) |"
}
$subnets = Invoke-AwsJson @("ec2", "describe-subnets", "--filters", "Name=vpc-id,Values=$vpcId", "--region", $R, "--output", "json")
if ($subnets -and $subnets.Subnets) {
    Out-Md ""
    Out-Md "| SubnetId | CidrBlock | AZ |"
    Out-Md "|----------|-----------|-----|"
    foreach ($s in $subnets.Subnets) { Out-Md "| $($s.SubnetId) | $($s.CidrBlock) | $($s.AvailabilityZone) |" }
}
$igw = Invoke-AwsJson @("ec2", "describe-internet-gateways", "--filters", "Name=attachment.vpc-id,Values=$vpcId", "--region", $R, "--output", "json")
Out-Md ""
Out-Md "IGW: $(if ($igw -and $igw.InternetGateways -and $igw.InternetGateways.Count -gt 0) { $igw.InternetGateways[0].InternetGatewayId } else { 'none' })"
$nat = Invoke-AwsJson @("ec2", "describe-nat-gateways", "--filter", "Name=vpc-id,Values=$vpcId", "Name=state,Values=available", "--region", $R, "--output", "json")
Out-Md "NAT(available): $(if ($nat -and $nat.NatGateways -and $nat.NatGateways.Count -gt 0) { ($nat.NatGateways | ForEach-Object { $_.NatGatewayId }) -join ', ' } else { 'none' })"
Out-Md ""

Out-Md "---"
Out-Md "*This report was generated by gather_fullstack_state.ps1 and does not modify infrastructure.*"

$outPath = Join-Path $OutDir "FULLSTACK-CURRENT-STATE-REPORT.md"
$utf8 = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText($outPath, $sb.ToString(), $utf8)
Write-Host "Report written: $outPath" -ForegroundColor Green
