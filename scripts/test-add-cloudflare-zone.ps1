# Offline contract for new and existing production zones.
$ErrorActionPreference = 'Stop'
$scriptPath = Join-Path $PSScriptRoot 'add-cloudflare-zone.ps1'
$fixtureAccount = '0123456789abcdef0123456789abcdef'
$env:CLOUDFLARE_ACCOUNT_ID = $fixtureAccount
$env:CLOUDFLARE_EMAIL = 'operator@example.test'
$env:CLOUDFLARE_API_KEY = 'synthetic-test-key'
$unicode = "$([char]0xD559)$([char]0xC6D0).test"
$ascii = [Globalization.IdnMapping]::new().GetAscii($unicode)
$fixtureZone = @{ id='fedcba9876543210fedcba9876543210'; name=$unicode; account=@{id=$fixtureAccount} }
$state = @{ exists=$false; mutations=0; details=0; reject=$false; hide=$false; status='pending'; detailAccount=$fixtureAccount; detailHost=$unicode; servers=@('one.ns.test','two.ns.test') }
function Assert($ok, $message) { if (-not $ok) { throw $message } }
function Invoke-RestMethod($Uri,$Method,$Headers,$ContentType,$Body) {
    $url = [uri]$Uri
    Assert ($Headers['X-Auth-Key'] -eq 'synthetic-test-key') 'Unexpected credential.'
    if ($Method -eq 'Post') {
        $state.mutations++
        $inputZone = $Body | ConvertFrom-Json
        Assert ($url.AbsolutePath -eq '/client/v4/zones' -and $inputZone.account.id -eq $fixtureAccount -and $inputZone.name -eq $ascii -and $inputZone.type -eq 'full' -and $inputZone.jump_start) 'Incorrect zone creation target.'
        if ($state.reject) { throw 'private-provider-response' }
        $state.exists = $true
        return @{success=$true;result=@{name_servers=@('unverified','unverified')}}
    }
    Assert ($Method -eq 'Get') 'Unexpected provider mutation.'
    if ($url.AbsolutePath -eq '/client/v4/zones') {
        Assert ($url.Query -match [regex]::Escape("name=$ascii&account.id=$fixtureAccount")) 'Wrong zone lookup.'
        $zones = if ($state.exists -and -not $state.hide) { $fixtureZone }
        return @{success=$true;result=@($zones | Where-Object { $_ })}
    }
    Assert ($url.AbsolutePath -eq "/client/v4/zones/$($fixtureZone.id)") 'Unexpected provider path.'
    $state.details++
    return @{success=$true;result=@{id=$fixtureZone.id;name=$state.detailHost;account=@{id=$state.detailAccount};status=$state.status;name_servers=$state.servers}}
}
function Expect-Failure($pattern) {
    $failure = ''
    try { & $scriptPath -Domain $ascii -Confirm:$false 6>$null } catch { $failure = $_.Exception.Message }
    Assert ($failure -like $pattern -and $failure -notlike '*private-provider-response*') "Wrong failure boundary: $failure"
}
& $scriptPath -Domain $unicode -WhatIf
Assert ($state.mutations -eq 0 -and $state.details -eq 0) 'WhatIf created a missing zone.'
$out = (& $scriptPath -Domain $unicode -Confirm:$false 6>&1) -join ' '
Assert ($out -like '*1차: one.ns.test*2차: two.ns.test*' -and $state.mutations -eq 1 -and $state.details -eq 1) 'Missing verified nameservers after create.'
$out = (& $scriptPath -Domain $ascii -WhatIf 6>&1) -join ' '
Assert ($out -like '*1차: one.ns.test*2차: two.ns.test*' -and $state.mutations -eq 1 -and $state.details -eq 2) 'Existing zone was not verified read-only.'
$fixtureZone.account.id = 'other'
Expect-Failure 'Expected one matching zone*'
$fixtureZone.account.id = $fixtureAccount
$state.detailHost = 'different.test'
Expect-Failure 'Zone identity*'
$state.detailHost = $unicode
$state.detailAccount = 'other'
Expect-Failure 'Zone identity*'
$state.detailAccount = $fixtureAccount
$state.servers = @('one.ns.test','ONE.NS.TEST.')
Expect-Failure 'Expected two distinct assigned*'
$state.exists = $false
$state.reject = $true
Expect-Failure 'Cloudflare Post zones failed*'
$state.reject = $false
$state.hide = $true
Expect-Failure 'Zone creation*has not been confirmed*'
Write-Output 'CLOUDFLARE_ZONE_CONTRACT_PASS'
