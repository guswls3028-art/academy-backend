# ==============================================================================
# SSOT: Instance key mapping - used by full_redeploy, deploy_worker_asg, deploy_preflight
# Video = AWS Batch 전용. EC2 배포 대상은 api, ai-worker-cpu, messaging-worker 3대만.
# ==============================================================================
$INSTANCE_KEY_FILES = @{
    "academy-api"                = "backend-api-key.pem"
    "academy-ai-worker-cpu"      = "ai-worker-key.pem"
    "academy-messaging-worker"   = "message-key.pem"
}
function Get-KeyPairName { param([string]$PemFile) return $PemFile -replace '\.pem$','' }
