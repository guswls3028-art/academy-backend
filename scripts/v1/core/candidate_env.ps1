# Candidate environment boundaries shared by CI and manual deployment.
# Preprod intentionally retains the production-shaped CDN/R2 read path used by
# the playback canary. All notification, billing, and external-AI mutation
# credentials are removed before the versioned SSM value is published.
$ErrorActionPreference = "Stop"

function Set-IsolatedPreprodR2Values {
    param(
        [Parameter(Mandatory = $true)][object]$Target,
        [Parameter(Mandatory = $true)][object]$Credential,
        [Parameter(Mandatory = $true)][string]$ProductionAccessKey,
        [Parameter(Mandatory = $true)][string]$ProductionSecretKey,
        [Parameter(Mandatory = $true)][string]$ProductionVideoBucket
    )
    $accessMode = [string]$Credential.ACCESS_MODE
    $endpoint = ([string]$Credential.R2_ENDPOINT).Trim().TrimEnd("/")
    $region = ([string]$Credential.R2_REGION).Trim()
    $accessKey = ([string]$Credential.R2_ACCESS_KEY).Trim()
    $secretKey = ([string]$Credential.R2_SECRET_KEY).Trim()
    $videoBucket = ([string]$Credential.R2_VIDEO_BUCKET).Trim()
    if (
        $accessMode -ne "read-only" -or
        -not $endpoint -or
        -not $region -or
        -not $accessKey -or
        -not $secretKey -or
        -not $videoBucket
    ) {
        throw "Preprod R2 credential must declare ACCESS_MODE=read-only and a complete video read contract."
    }
    if ($videoBucket -ne $ProductionVideoBucket) {
        throw "Preprod R2 credential must target the production video bucket used by the playback canary."
    }
    if ($accessKey -eq $ProductionAccessKey -or $secretKey -eq $ProductionSecretKey) {
        throw "Preprod R2 credential must not reuse the production R2 key pair."
    }
    $values = [ordered]@{
        ACADEMY_R2_ACCESS_MODE = "read-only"
        R2_ENDPOINT = $endpoint
        R2_REGION = $region
        R2_ACCESS_KEY = $accessKey
        R2_SECRET_KEY = $secretKey
        R2_VIDEO_BUCKET = $videoBucket
        R2_AI_BUCKET = ""
        R2_STORAGE_BUCKET = ""
        R2_EXCEL_BUCKET = ""
        R2_ADMIN_BUCKET = ""
    }
    foreach ($entry in $values.GetEnumerator()) {
        $Target | Add-Member -NotePropertyName $entry.Key -NotePropertyValue $entry.Value -Force
    }
}

function Assert-IsolatedPreprodR2Values {
    param(
        [Parameter(Mandatory = $true)][object]$Target,
        [Parameter(Mandatory = $true)][object]$Credential
    )
    $expected = [ordered]@{
        ACADEMY_R2_ACCESS_MODE = "read-only"
        R2_ENDPOINT = ([string]$Credential.R2_ENDPOINT).Trim().TrimEnd("/")
        R2_REGION = ([string]$Credential.R2_REGION).Trim()
        R2_ACCESS_KEY = ([string]$Credential.R2_ACCESS_KEY).Trim()
        R2_SECRET_KEY = ([string]$Credential.R2_SECRET_KEY).Trim()
        R2_VIDEO_BUCKET = ([string]$Credential.R2_VIDEO_BUCKET).Trim()
        R2_AI_BUCKET = ""
        R2_STORAGE_BUCKET = ""
        R2_EXCEL_BUCKET = ""
        R2_ADMIN_BUCKET = ""
    }
    foreach ($entry in $expected.GetEnumerator()) {
        if ([string]$Target.($entry.Key) -cne [string]$entry.Value) {
            throw "Preprod R2 value does not match the dedicated read-only contract: $($entry.Key)"
        }
    }
}

function Set-IsolatedPreprodApiValues {
    param(
        [Parameter(Mandatory = $true)][object]$Target,
        [Parameter(Mandatory = $true)][string]$ReleaseId,
        [Parameter(Mandatory = $true)][string]$CredentialPassword
    )
    if ($ReleaseId -notmatch '^(?:sha-[0-9a-fA-F]{40}-run-[0-9]+-[0-9]+|manual-sha256-[0-9a-fA-F]{64})$') {
        throw "Invalid preprod release ID."
    }
    if (-not $CredentialPassword -or $CredentialPassword.Length -lt 32) {
        throw "Preprod credential password is missing or too short."
    }

    $secretPatterns = @(
        '^SOLAPI_',
        '^TOSS_',
        '^BILLING_',
        '^DEV_ALERTS_',
        '^VAPID_PRIVATE_KEY$',
        '^OPENAI_',
        '^ANTHROPIC_',
        '^AWS_ACCESS_KEY_ID$',
        '^AWS_SECRET_ACCESS_KEY$',
        '^AWS_SESSION_TOKEN$',
        '^AWS_ROOT_'
    )
    foreach ($property in @($Target.PSObject.Properties)) {
        if ($secretPatterns | Where-Object { $property.Name -match $_ }) {
            $Target.PSObject.Properties.Remove($property.Name)
        }
    }

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $djangoSecret = [Convert]::ToBase64String(
            $sha256.ComputeHash(
                [Text.Encoding]::UTF8.GetBytes(
                    "academy-preprod-django:$CredentialPassword"
                )
            )
        )
        $bindingSecret = [Convert]::ToBase64String(
            $sha256.ComputeHash(
                [Text.Encoding]::UTF8.GetBytes(
                    "academy-preprod-messaging:$CredentialPassword"
                )
            )
        )
    } finally {
        $sha256.Dispose()
    }

    $values = [ordered]@{
        ACADEMY_RUNTIME_ENV = "preprod"
        ACADEMY_PREPROD_RELEASE_ID = $ReleaseId
        SECRET_KEY = $djangoSecret
        MESSAGING_TENANT_BINDING_KEY = $bindingSecret
        MESSAGING_TENANT_BINDING_FALLBACK_KEYS = ""
        SOLAPI_MOCK = "true"
        SOLAPI_API_KEY = ""
        SOLAPI_API_SECRET = ""
        SOLAPI_SENDER = ""
        SOLAPI_KAKAO_PF_ID = ""
        SOLAPI_KAKAO_TEMPLATE_ID = ""
        MESSAGING_DRY_RUN_TRIGGERS = "*"
        TOSS_AUTO_BILLING_ENABLED = "false"
        TOSS_PAYMENTS_CLIENT_KEY = ""
        TOSS_PAYMENTS_SECRET_KEY = ""
        BILLING_BANK_TRANSFER_ENABLED = "false"
        BILLING_KEY_ENCRYPTION_WRITE_ENABLED = "false"
        BILLING_KEY_ENCRYPTION_PRIMARY_KEY = ""
        BILLING_KEY_ENCRYPTION_FALLBACK_KEYS = ""
        VAPID_PRIVATE_KEY = ""
        OPENAI_API_KEY = ""
        ANTHROPIC_API_KEY = ""
        AWS_ACCESS_KEY_ID = ""
        AWS_SECRET_ACCESS_KEY = ""
        AWS_SESSION_TOKEN = ""
        SENTRY_ENVIRONMENT = "preprod"
    }
    foreach ($entry in $values.GetEnumerator()) {
        $Target | Add-Member -NotePropertyName $entry.Key -NotePropertyValue $entry.Value -Force
    }
    $Target.PSObject.Properties.Remove("ACADEMY_DEVELOPMENT_RELEASE_ID")
}

function Assert-IsolatedPreprodApiValues {
    param(
        [Parameter(Mandatory = $true)][object]$Target,
        [Parameter(Mandatory = $true)][string]$ReleaseId
    )
    if (
        [string]$Target.ACADEMY_RUNTIME_ENV -ne "preprod" -or
        [string]$Target.ACADEMY_PREPROD_RELEASE_ID -ne $ReleaseId -or
        [string]$Target.SOLAPI_MOCK -ne "true" -or
        [string]$Target.MESSAGING_DRY_RUN_TRIGGERS -ne "*" -or
        [string]$Target.TOSS_AUTO_BILLING_ENABLED -ne "false" -or
        [string]$Target.BILLING_BANK_TRANSFER_ENABLED -ne "false" -or
        [string]$Target.BILLING_KEY_ENCRYPTION_WRITE_ENABLED -ne "false"
    ) {
        throw "Preprod fail-closed runtime flags do not match the required boundary."
    }
    $forbiddenNonEmpty = @(
        "SOLAPI_API_KEY",
        "SOLAPI_API_SECRET",
        "TOSS_PAYMENTS_CLIENT_KEY",
        "TOSS_PAYMENTS_SECRET_KEY",
        "BILLING_KEY_ENCRYPTION_PRIMARY_KEY",
        "BILLING_KEY_ENCRYPTION_FALLBACK_KEYS",
        "VAPID_PRIVATE_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN"
    )
    foreach ($name in $forbiddenNonEmpty) {
        if ([string]$Target.$name) {
            throw "Preprod env retains forbidden credential: $name"
        }
    }
    if (
        -not [string]$Target.SECRET_KEY -or
        -not [string]$Target.MESSAGING_TENANT_BINDING_KEY -or
        [string]$Target.ACADEMY_R2_ACCESS_MODE -ne "read-only" -or
        -not [string]$Target.R2_ACCESS_KEY -or
        -not [string]$Target.R2_SECRET_KEY -or
        -not [string]$Target.R2_VIDEO_BUCKET
    ) {
        throw "Preprod env must use isolated signing secrets and the dedicated read-only R2 contract."
    }
}
