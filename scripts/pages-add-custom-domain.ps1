# Connect the delegated production apex and www to the existing Academy Pages project.
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param([Parameter(Mandatory)][string]$Domain)

$ErrorActionPreference = 'Stop'
function Get-AsciiHost([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return '' }
    return ([Globalization.IdnMapping]::new().GetAscii($Value.Trim().TrimEnd('.'))).ToLowerInvariant()
}
$Domain = Get-AsciiHost $Domain
if ($Domain -notmatch '^(?=.{1,253}$)(?!www\.)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]+$') {
    throw 'Provide the exact apex hostname.'
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

function Get-PagesBindings {
    foreach ($binding in (Get-CloudflareList $domainsPath)) {
        [pscustomobject]@{ name = Get-AsciiHost $binding.name; status = $binding.status }
    }
}

function Get-DnsState($Name) {
    $records = @(Get-CloudflareList "$dnsPath`?name=$Name" | Where-Object { (Get-AsciiHost $_.name) -eq $Name })
    $routing = @($records | Where-Object { $_.type -in 'A','AAAA','CNAME','NS' })
    if (-not $routing.Count) { return 'missing' }
    if ($routing.Count -eq 1 -and $routing[0].type -eq 'CNAME' -and
        (Get-AsciiHost $routing[0].content) -eq $target -and $routing[0].proxied -eq $true) { return 'present' }
    throw "Conflicting DNS routing record for $Name. Existing records were preserved."
}

$access = Get-CloudflareAccess
$zones = @(Get-CloudflareList "zones?name=$Domain&account.id=$($access.Account)")
if ($zones.Count -ne 1 -or $zones[0].id -notmatch '^[0-9a-f]{32}$' -or (Get-AsciiHost $zones[0].name) -ne $Domain -or $zones[0].account.id -ne $access.Account -or $zones[0].status -ne 'active') {
    throw "Expected one active zone for $Domain in the configured account."
}
$zone = $zones[0]
$expected = @($zone.name_servers | ForEach-Object { Get-AsciiHost $_ } | Sort-Object -Unique)
if ($expected.Count -lt 2) { throw 'Missing assigned authoritative nameservers.' }
foreach ($resolver in '1.1.1.1','8.8.8.8') {
    $observed = @(Resolve-DnsName -Name $Domain -Type NS -Server $resolver -DnsOnly |
        Where-Object { $_.NameHost -and (Get-AsciiHost $_.Name) -eq $Domain } |
        ForEach-Object { Get-AsciiHost $_.NameHost } | Sort-Object -Unique)
    if (Compare-Object $expected $observed) { throw "Delegation for $Domain is not confirmed at $resolver." }
}
$project = 'academy-frontend'
$projectPath = "accounts/$($access.Account)/pages/projects/$project"
$target = 'academy-frontend-26b.pages.dev'
$projectState = (Invoke-Cloudflare $projectPath).result
if ($projectState.name -ne $project -or (Get-AsciiHost $projectState.subdomain) -ne $target -or $projectState.canonical_deployment.environment -ne 'production') { throw 'Unexpected Pages project, assigned subdomain or canonical deployment environment.' }
$domainsPath = "$projectPath/domains"
$dnsPath = "zones/$($zone.id)/dns_records"
$names = @($Domain, "www.$Domain")
$bindings = @(Get-PagesBindings)
foreach ($name in $names) {
    $entry = @($bindings | Where-Object name -eq $name)
    if ($entry.Count -gt 1 -or ($entry.Count -eq 1 -and $entry[0].status -notin 'active','pending','initializing')) {
        throw "Existing Pages binding for $name requires provider review."
    }
    $null = Get-DnsState $name
}
foreach ($name in $names) {
    if ($name -notin $bindings.name) {
        if ($PSCmdlet.ShouldProcess($name, "Add Cloudflare Pages custom domain")) {
            try {
                $null = Invoke-Cloudflare $domainsPath Post @{ name = $name }
            } catch {
                $entry = @(Get-PagesBindings | Where-Object name -eq $name)
                if ($entry.Count -ne 1 -or $entry[0].status -notin 'active','pending','initializing') { throw }
            }
        } elseif (-not $WhatIfPreference) { continue }
    }
    if ((Get-DnsState $name) -eq 'missing' -and $PSCmdlet.ShouldProcess("$name -> $target", "Create proxied Cloudflare CNAME")) {
        try {
            $null = Invoke-Cloudflare $dnsPath Post @{ type = 'CNAME'; name = $name; content = $target; ttl = 1; proxied = $true }
        } catch {
            if ((Get-DnsState $name) -ne 'present') { throw }
        }
    }
}
if ($WhatIfPreference) { return }
$bindings = @(Get-PagesBindings)
foreach ($name in $names) {
    $entry = @($bindings | Where-Object name -eq $name)
    if ($entry.Count -ne 1 -or (Get-DnsState $name) -ne 'present') { throw "Incomplete Pages or DNS readback for $name." }
    $status = [string]$entry[0].status
    if ($status -notin 'active','pending','initializing') { throw "Unexpected Pages status for ${name}: $status." }
    [pscustomobject]@{ Domain = $name; PagesStatus = $status; ProxiedCname = $target }
}
