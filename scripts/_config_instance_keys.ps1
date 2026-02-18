# ==============================================================================
# SSOT: Instance key mapping - used by full_redeploy, deploy_worker_asg, deploy_preflight
# Key file = .pem path relative to KeyDir. AWS KeyPair name = filename without .pem
# ==============================================================================
$INSTANCE_KEY_FILES = @{
    "academy-api"                = "backend-api-key.pem"
    "academy-ai-worker-cpu"      = "ai-worker-key.pem"
    "academy-video-worker"       = "video-worker-key.pem"
    "academy-messaging-worker"   = "message-key.pem"
}
function Get-KeyPairName { param([string]$PemFile) return $PemFile -replace '\.pem$','' }
