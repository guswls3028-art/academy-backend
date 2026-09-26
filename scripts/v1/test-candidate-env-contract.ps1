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
    GEMINI_API_KEY = "production-gemini-secret"
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
    [string]$source.MESSAGING_TENANT_BINDING_KEY -eq "production-binding-secret" -or
    [string]$source.GEMINI_API_KEY
) {
    throw "Preprod sanitizer must replace production signing secrets and remove Gemini."
}

$developmentScript = Join-Path $PSScriptRoot "publish-api-development-env.ps1"
$parseTokens = $null
$parseErrors = $null
$developmentAst = [System.Management.Automation.Language.Parser]::ParseFile(
    $developmentScript, [ref]$parseTokens, [ref]$parseErrors
)
if ($parseErrors.Count) { throw "Development publisher must parse before contract execution." }
$developmentFunction = $developmentAst.Find({
    param($node)
    $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
    $node.Name -eq "Set-IsolatedDevelopmentValues"
}, $false)
if (-not $developmentFunction) { throw "Development sanitizer is missing." }
# Load only the real sanitizer function; do not run publisher AWS operations.
. ([scriptblock]::Create($developmentFunction.Extent.Text))
$credentialPassword = "development-db-test-secret"
$credentialUser = "academy_api_development_app"
$developmentDatabaseName = "academy_api_development"
$r2Endpoint = "https://example.r2.cloudflarestorage.com"
$r2Region = "auto"
$r2AccessKey = "development-r2-test-key"
$r2SecretKey = "development-r2-test-secret"
$r2Bucket = "academy-development-artifacts"
$script:Region = "ap-northeast-2"
$script:ApiDevelopmentCdnSigningSecret = "development-signing-test-secret"
$script:ApiDevelopmentAiQueueName = "academy-development-ai"
$script:ApiDevelopmentToolsQueueName = "academy-development-tools"
$script:ApiDevelopmentMessagingQueueName = "academy-development-messaging"
foreach ($settings in @("apps.api.config.settings.development", "apps.api.config.settings.worker")) {
    $target = [pscustomobject]@{
        GEMINI_API_KEY = "approved-existing-provider-test-key"
        GEMINI_OTHER_SECRET = "must-not-be-retained"
        OPENAI_API_KEY = "must-not-be-retained"
        AWS_ACCESS_KEY_ID = "must-not-be-retained"
        DB_NAME = "production"
        R2_STORAGE_BUCKET = "production"
        TOOLS_SQS_QUEUE_NAME = "production"
        SOLAPI_MOCK = "false"
    }
    Set-IsolatedDevelopmentValues -Target $target -SettingsModule $settings
    $expectedKey = if ($settings -eq "apps.api.config.settings.worker") {
        "approved-existing-provider-test-key"
    } else { "" }
    if (
        [string]$target.GEMINI_API_KEY -ne $expectedKey -or
        [string]$target.GEMINI_OTHER_SECRET -or [string]$target.OPENAI_API_KEY -or
        [string]$target.AWS_ACCESS_KEY_ID -or
        [string]$target.DB_NAME -ne $developmentDatabaseName -or
        [string]$target.R2_STORAGE_BUCKET -ne $r2Bucket -or
        [string]$target.TOOLS_SQS_QUEUE_NAME -ne $script:ApiDevelopmentToolsQueueName -or
        [string]$target.SOLAPI_MOCK -ne "true"
    ) {
        throw "Approved Gemini reuse must remain worker-only and preserve development isolation."
    }
}

Write-Host "CANDIDATE_ENV_CONTRACT_PASS" -ForegroundColor Green
