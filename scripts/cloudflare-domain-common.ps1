function Get-AsciiHost([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    return ([Globalization.IdnMapping]::new().GetAscii($Value.Trim().TrimEnd('.'))).ToLowerInvariant()
}
function Get-CloudflareApex([string]$Value) {
    $hostname = Get-AsciiHost $Value
    if ($hostname -notmatch '^(?=.{1,253}$)(?!www\.)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]+$') {
        throw 'Provide the exact apex hostname.'
    }
    return $hostname
}

function Get-CloudflareAccess {
    $email = $env:CLOUDFLARE_EMAIL
    $key = $env:CLOUDFLARE_API_KEY
    $account = $env:CLOUDFLARE_ACCOUNT_ID
    if (-not ($email -and $key -and $account)) {
        $directory = Join-Path $env:USERPROFILE '.codex/external-project-secrets'
        $metadata = Join-Path $directory 'cloudflare-auth.json'
        if (Test-Path -LiteralPath $metadata) {
            $config = Get-Content -LiteralPath $metadata -Raw | ConvertFrom-Json
            $email = $config.email
            $account = $config.account_id
            $blob = Join-Path $directory 'cloudflare-global-api-key.dpapi'
            if ([IO.Path]::GetFileName([string]$config.key_file) -ne 'cloudflare-global-api-key.dpapi') {
                throw 'Unrecognized Cloudflare credential file in the configured metadata.'
            }
            $secure = (Get-Content -LiteralPath $blob -Raw).Trim() | ConvertTo-SecureString
            try { $key = [Net.NetworkCredential]::new('', $secure).Password } finally { $secure.Dispose() }
        } else {
            $envFile = Join-Path $PSScriptRoot '../.env'
            if (Test-Path -LiteralPath $envFile) {
                foreach ($line in (Get-Content -LiteralPath $envFile)) {
                    if ($line -match '^CLOUDFLARE_(EMAIL|API_KEY|ACCOUNT_ID)=(.+)$') {
                        switch ($Matches[1]) {
                            EMAIL { $email = $Matches[2].Trim() }
                            API_KEY { $key = $Matches[2].Trim() }
                            ACCOUNT_ID { $account = $Matches[2].Trim() }
                        }
                    }
                }
            }
        }
    }
    if ($account -notmatch '^[0-9a-f]{32}$' -or -not $email -or -not $key) {
        throw 'A complete configured Cloudflare account and credential are required.'
    }
    @{ Account = $account; Headers = @{ 'X-Auth-Email' = $email; 'X-Auth-Key' = $key } }
}

function Invoke-Cloudflare($Path, $Method = 'Get', $Payload) {
    $params = @{ Uri = "https://api.cloudflare.com/client/v4/$Path"; Headers = $access.Headers; Method = $Method }
    if ($null -ne $Payload) {
        $params.ContentType = 'application/json'
        $params.Body = ConvertTo-Json $Payload -Compress
    }
    try { $response = Invoke-RestMethod @params } catch {
        throw "Cloudflare $Method $Path failed. Check provider state before retrying."
    }
    if ($response.success -ne $true) { throw "Cloudflare rejected $Method $Path." }
    return $response
}

function Get-CloudflareList($Path) {
    $separator = if ($Path.Contains('?')) { '&' } else { '?' }
    for ($page = 1; ; $page++) {
        $response = Invoke-Cloudflare ("${Path}${separator}per_page=25&page=$page")
        foreach ($item in $response.result) { $item }
        if ($response.result_info.total_pages) {
            if ($page -ge $response.result_info.total_pages) { break }
        } elseif (@($response.result).Count -lt 25) { break }
    }
}

function Get-ExactCloudflareZone {
    $zones = @(Get-CloudflareList "zones?name=$Domain&account.id=$($access.Account)")
    if ($zones.Count -eq 0) { return }
    if ($zones.Count -ne 1 -or $zones[0].id -notmatch '^[0-9a-f]{32}$' -or
        (Get-AsciiHost $zones[0].name) -ne $Domain -or $zones[0].account.id -ne $access.Account) {
        throw "Expected one matching zone for $Domain in the configured account."
    }
    return $zones[0]
}
