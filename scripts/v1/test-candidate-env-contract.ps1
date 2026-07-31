$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "core\candidate_env.ps1")

$releaseId = "manual-sha256-$("a" * 64)"
$source = [pscustomobject]@{
    DJANGO_SETTINGS_MODULE = "apps.api.config.settings.prod"
    DB_NAME = "academy_api"
    DB_USER = "academy_api_app"
    DB_PASSWORD = "production-db-secret"
    R2_ENDPOINT = "https://example.r2.cloudflarestorage.com"
    R2_ACCESS_KEY = "read-path-key-retained-for-playback-canary"
    R2_SECRET_KEY = "read-path-secret-retained-for-playback-canary"
    R2_VIDEO_BUCKET = "academy-video"
    CDN_HLS_SIGNING_SECRET = "production-shaped-playback-secret"
    CDN_HLS_SIGNING_KEY_ID = "v1"
    SOLAPI_API_KEY = "live-solapi-key"
    SOLAPI_API_SECRET = "live-solapi-secret"
    TOSS_PAYMENTS_CLIENT_KEY = "live_ck_should_be_removed"
    TOSS_PAYMENTS_SECRET_KEY = "live_sk_should_be_removed"
    TOSS_AUTO_BILLING_ENABLED = "true"
    OPENAI_API_KEY = "external-ai-secret"
    ANTHROPIC_API_KEY = "external-ai-secret"
    AWS_ACCESS_KEY_ID = "static-key"
    AWS_SECRET_ACCESS_KEY = "static-secret"
    SECRET_KEY = "production-django-secret"
    MESSAGING_TENANT_BINDING_KEY = "production-binding-secret"
}
$preprodR2 = [pscustomobject]@{
    ACCESS_MODE = "read-only"
    R2_ENDPOINT = "https://example.r2.cloudflarestorage.com"
    R2_REGION = "auto"
    R2_ACCESS_KEY = "dedicated-preprod-read-key"
    R2_SECRET_KEY = "dedicated-preprod-read-secret"
    R2_VIDEO_BUCKET = "academy-video"
}

Set-IsolatedPreprodR2Values `
    -Target $source `
    -Credential $preprodR2 `
    -ProductionAccessKey ([string]$source.R2_ACCESS_KEY) `
    -ProductionSecretKey ([string]$source.R2_SECRET_KEY) `
    -ProductionVideoBucket ([string]$source.R2_VIDEO_BUCKET)
Set-IsolatedPreprodApiValues `
    -Target $source `
    -ReleaseId $releaseId `
    -CredentialPassword ("p" * 48)
Assert-IsolatedPreprodApiValues -Target $source -ReleaseId $releaseId
Assert-IsolatedPreprodR2Values -Target $source -Credential $preprodR2

if (
    [string]$source.R2_ACCESS_KEY -ne "dedicated-preprod-read-key" -or
    [string]$source.R2_SECRET_KEY -ne "dedicated-preprod-read-secret" -or
    [string]$source.CDN_HLS_SIGNING_KEY_ID -ne "v1"
) {
    throw "Preprod sanitizer must use the dedicated read-only playback credential."
}
if (
    [string]$source.SECRET_KEY -eq "production-django-secret" -or
    [string]$source.MESSAGING_TENANT_BINDING_KEY -eq "production-binding-secret"
) {
    throw "Preprod sanitizer must replace production signing secrets."
}

Write-Host "CANDIDATE_ENV_CONTRACT_PASS" -ForegroundColor Green
