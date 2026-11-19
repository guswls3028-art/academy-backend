# Connect the delegated production apex and www to the existing Academy Pages project.
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param([Parameter(Mandatory)][string]$Domain)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'cloudflare-domain-common.ps1')
$Domain = Get-CloudflareApex $Domain

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
$zone = Get-ExactCloudflareZone
if (-not $zone -or $zone.status -ne 'active') { throw "Expected one active zone for $Domain in the configured account." }
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
