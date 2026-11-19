# Offline provider contract. All account values and records are synthetic.
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'pages-add-custom-domain.ps1'
$domain = 'example.test'
$target = 'academy-frontend-26b.pages.dev'
$account = '0123456789abcdef0123456789abcdef'
$env:CLOUDFLARE_EMAIL = 'operator@example.test'
$env:CLOUDFLARE_API_KEY = 'synthetic-test-key'
$env:CLOUDFLARE_ACCOUNT_ID = $account

function Assert($ok, $message) { if (-not $ok) { throw $message } }
function Reset-Fixture {
    $script:fixtureRecords = @{}
    $script:fixtureBindings = @{}
    $script:delegate = 'one.ns.test'
    $script:projectTarget = $target
    $script:environmentName = 'production'
    $script:zoneName = $domain
    $script:answerName = $domain
    $script:zoneStatus = 'active'
    $script:zoneAccount = $account
    $script:writes = [Collections.Generic.List[string]]::new()
    $script:failWrite = 0
    $script:dropDns = $false
    $script:concurrentRegistration = $false
}
function Resolve-DnsName($Name,$Type,$Server,[switch]$DnsOnly) {
    @($delegate,'two.ns.test') | ForEach-Object { @{Name=$answerName;NameHost=$_;Type='NS'} }
}
function Invoke-RestMethod($Uri,$Headers,$Method,$Body,$ContentType) {
    Assert ($Headers['X-Auth-Key'] -eq 'synthetic-test-key') 'Unexpected credential.'
    $url = [uri]$Uri
    $path = $url.AbsolutePath
    $payload = if ($Body) { $Body | ConvertFrom-Json }
    if ($Method -eq 'Post') {
        $writes.Add($path)
        if ($failWrite -eq $writes.Count) { return @{success=$false} }
        if ($path.EndsWith('/domains')) {
            $fixtureBindings[$payload.name] = 'pending'
            if ($concurrentRegistration -and $payload.name -eq $domain) {
                $fixtureRecords[$domain] = [pscustomobject]@{name=$domain;type='CNAME';content=$target;proxied=$true}
                return @{success=$false}
            }
        }
        elseif ($path.EndsWith('/dns_records')) {
            if (-not $dropDns) { $fixtureRecords[$payload.name] = $payload }
            if ($concurrentRegistration) { return @{success=$false} }
        } else { throw 'Unexpected mutation' }
        return @{success=$true;result=$payload}
    }
    Assert ($Method -eq 'Get') 'Unexpected destructive method.'
    $data = switch -Wildcard ($path) {
        '*/zones' { @{id='fedcba9876543210fedcba9876543210';name=$zoneName;account=@{id=$zoneAccount};status=$zoneStatus;name_servers=@('one.ns.test','two.ns.test')} }
        '*/dns_records' {
            foreach ($key in $fixtureRecords.Keys) { $fixtureRecords[$key] }
        }
        '*/projects/academy-frontend' { @{name='academy-frontend';subdomain=$projectTarget;canonical_deployment=@{environment=$environmentName}} }
        '*/domains' {
            Assert ($url.Query -match 'per_page=25') 'Unsupported Pages page size.'

            return @{success=$true;result=@($(
                if ($url.Query -match 'page=2(?:&|$)') { foreach ($key in $fixtureBindings.Keys) { [pscustomobject]@{name=$key;status=$fixtureBindings[$key]} } }
            ));result_info=@{total_pages=2}}
        }
        default { throw "Unexpected path: $path" }
    }
    return @{success=$true;result=@($data)}
}
function Expect-Failure($pattern) {
    $message = ''
    try { & $scriptPath -Domain $domain -Confirm:$false | Out-Null } catch { $message = $_.Exception.Message }
    Assert ($message -like $pattern) "Expected $pattern, got $message"
}
Reset-Fixture
& $scriptPath -Domain $domain -WhatIf
Assert ($writes.Count -eq 0) 'WhatIf wrote provider state.'
$result = @(& $scriptPath -Domain $domain -Confirm:$false)
Assert ($writes.Count -eq 4 -and $result.Count -eq 2 -and $result[0].PagesStatus -eq 'pending') "Unexpected initial result writes=$writes count=$($result.Count) status=$($result[0].PagesStatus)"
$fixtureBindings[$domain] = 'active'
$result = @(& $scriptPath -Domain $domain -Confirm:$false)
Assert ($writes.Count -eq 4 -and $result[0].PagesStatus -eq 'active') 'Duplicate write or invalid final readback.'
Reset-Fixture
$environmentName = 'preview'
Expect-Failure 'Unexpected Pages project*'
Assert ($writes.Count -eq 0) 'Changed provider on preview canonical deployment.'
# Build a Unicode label without relying on the PowerShell 5.1 source file encoding.
$unicode = "$([char]0xD559)$([char]0xC6D0).test"
$ascii = [Globalization.IdnMapping]::new().GetAscii($unicode)
$zoneName = "$unicode."
$answerName = $unicode
$environmentName = 'production'
$fixtureBindings[$unicode] = 'active'
$fixtureBindings["www.$ascii"] = 'pending'
$fixtureRecords[$ascii] = [pscustomobject]@{name=$ascii;type='CNAME';content="$target.";proxied=$true}
$fixtureRecords["www.$unicode"] = [pscustomobject]@{name="www.$unicode";type='CNAME';content=$target;proxied=$true}
foreach ($spelling in $unicode,$ascii) {
    $result = @(& $scriptPath -Domain $spelling -Confirm:$false)
    Assert ($result.Count -eq 2 -and $result[0].Domain -eq $ascii -and $writes.Count -eq 0) 'Mixed IDN spellings created an extra registration.'
}
Reset-Fixture
$concurrentRegistration = $true
$result = @(& $scriptPath -Domain $domain -Confirm:$false)
Assert ($writes.Count -eq 3 -and $result.Count -eq 2) 'Exact concurrent creations were not recovered, or automatic apex DNS was written twice.'
Reset-Fixture
$fixtureRecords["www.$domain"] = [pscustomobject]@{name="www.$domain";type='AAAA';content='::1'}
Expect-Failure 'Conflicting DNS*'
Assert ($writes.Count -eq 0) 'Changed apex before detecting www conflict.'
Reset-Fixture
$delegate = 'old.ns.test'
Expect-Failure 'Delegation*'
$delegate = 'one.ns.test'
$zoneStatus = 'pending'
Expect-Failure 'Expected one active zone*'
$zoneStatus = 'active'
$zoneAccount = 'other'
Expect-Failure 'Expected one active zone*'
$zoneAccount = $account
$projectTarget = 'other.pages.dev'
Expect-Failure 'Unexpected Pages project*'
Assert ($writes.Count -eq 0) 'Changed provider before identity/delegation confirmation.'
Reset-Fixture
$failWrite = 2
Expect-Failure 'Cloudflare rejected*'
$result = @(& $scriptPath -Domain $domain -Confirm:$false)
Assert ($writes.Count -eq 5 -and $result.Count -eq 2) 'Retry after partial failure did not converge.'
Reset-Fixture
$dropDns = $true
Expect-Failure 'Incomplete Pages or DNS readback*'
Reset-Fixture
$fixtureBindings[$domain] = 'error'
Expect-Failure 'Existing Pages binding*'
Assert ($writes.Count -eq 0) 'Changed a failed binding.'

# Replace all filesystem reads for the two credential discovery paths.
function Test-AuthenticationPath([bool]$useStore) {
    $env:CLOUDFLARE_EMAIL = $null
    $env:CLOUDFLARE_API_KEY = $null
    $env:CLOUDFLARE_ACCOUNT_ID = $null
    function Test-Path($LiteralPath) {
        if ($LiteralPath.EndsWith('cloudflare-auth.json')) { return $useStore }
        if ($LiteralPath.EndsWith('.env')) { return -not $useStore }
        throw 'Unexpected credential metadata lookup.'
    }
    function Get-Content($LiteralPath,[switch]$Raw) {
        if ($LiteralPath.EndsWith('cloudflare-auth.json') -and $useStore) {
            return @{email='operator@example.test';account_id='0123456789abcdef0123456789abcdef';key_file='cloudflare-global-api-key.dpapi'} | ConvertTo-Json
        }
        if ($LiteralPath.EndsWith('cloudflare-global-api-key.dpapi') -and $useStore) { return 'synthetic-blob' }
        if ($LiteralPath.EndsWith('.env') -and -not $useStore) {
            return 'CLOUDFLARE_EMAIL=operator@example.test','CLOUDFLARE_API_KEY=synthetic-test-key','CLOUDFLARE_ACCOUNT_ID=0123456789abcdef0123456789abcdef'
        }
        throw 'Unexpected credential read.'
    }
    function ConvertTo-SecureString {
        process {
            Assert ($_ -eq 'synthetic-blob') 'Unexpected DPAPI input.'
            Microsoft.PowerShell.Security\ConvertTo-SecureString 'synthetic-test-key' -AsPlainText -Force
        }
    }
    Reset-Fixture
    & $scriptPath -Domain $domain -WhatIf
    Assert ($writes.Count -eq 0) 'Credential discovery changed provider state.'
}
Test-AuthenticationPath $true
Test-AuthenticationPath $false
Write-Output 'PAGES_CUSTOM_DOMAIN_CONTRACT_PASS'
