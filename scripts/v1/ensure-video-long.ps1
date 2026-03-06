# ==============================================================================
# 3시간+ 영상용 Long 큐/CE/JobDef 생성
# ==============================================================================
# params.yaml 파서가 videoBatch.long 중첩을 로드하지 않아 deploy에서 스킵됨.
# 이 스크립트로 Long 리소스를 수동 생성.
# 사용: pwsh scripts/v1/ensure-video-long.ps1 -AwsProfile default
# ==============================================================================
param([string]$AwsProfile = "")

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "core\env.ps1")
if ($AwsProfile) { $env:AWS_PROFILE = $AwsProfile; if (-not $env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION = "ap-northeast-2" } }

. (Join-Path $PSScriptRoot "core\ssot.ps1")
. (Join-Path $PSScriptRoot "core\logging.ps1")
. (Join-Path $PSScriptRoot "core\aws.ps1")
. (Join-Path $PSScriptRoot "core\wait.ps1")
. (Join-Path $PSScriptRoot "resources\iam.ps1")
. (Join-Path $PSScriptRoot "resources\batch.ps1")
. (Join-Path $PSScriptRoot "resources\jobdef.ps1")
$null = Load-SSOT -Env "prod"
$script:BatchIam = Ensure-BatchIAM
if (-not $script:BatchSecurityGroupId) {
    $ce = aws batch describe-compute-environments --compute-environments academy-v1-video-batch-ce --region $script:Region --query "computeEnvironments[0].computeResources.securityGroupIds[0]" --output text 2>$null
    if ($ce) { $script:BatchSecurityGroupId = $ce.Trim() }
}
if (-not $script:BatchSecurityGroupId) { throw "BatchSecurityGroupId required. Ensure network/sg-batch exists." }

# params.yaml videoBatch.long 중첩 미지원 → 명시 설정
$script:VideoLongCEName = "academy-v1-video-batch-long-ce"
$script:VideoLongQueueName = "academy-v1-video-batch-long-queue"
$script:VideoLongJobDefName = "academy-v1-video-batch-long-jobdef"
$script:VideoLongMinvCpus = 0
$script:VideoLongMaxvCpus = 80
$script:VideoLongInstanceType = "c6g.xlarge"
$script:PlanMode = $false
$script:AllowRebuild = $true

Write-Host "=== Ensure Video Long (3시간+ 영상용) ===" -ForegroundColor Cyan
Ensure-VideoLongCE
Ensure-VideoLongQueue
Ensure-VideoLongJobDef
Write-Host "`nLong 큐/CE/JobDef 준비 완료. SSM /academy/api/env 에 VIDEO_BATCH_JOB_QUEUE_LONG, VIDEO_BATCH_JOB_DEFINITION_LONG 추가 확인." -ForegroundColor Green
