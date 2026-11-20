# Register the exact production zone and read its assigned nameservers from Cloudflare.
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param([Parameter(Mandatory)][string]$Domain)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'cloudflare-domain-common.ps1')
$Domain = Get-CloudflareApex $Domain
$access = Get-CloudflareAccess
$zone = Get-ExactCloudflareZone
if (-not $zone) {
    if (-not $PSCmdlet.ShouldProcess($Domain, 'Create Cloudflare zone with jump_start enabled')) { return }
    try {
        $null = Invoke-Cloudflare 'zones' Post @{ name = $Domain; account = @{ id = $access.Account }; jump_start = $true; type = 'full' }
    } catch {
        $zone = Get-ExactCloudflareZone
        if (-not $zone) { throw }
    }
    if (-not $zone) { $zone = Get-ExactCloudflareZone }
    if (-not $zone) { throw "Zone creation for $Domain has not been confirmed. Retry after checking provider state." }
}
$detail = (Invoke-Cloudflare "zones/$($zone.id)").result
if ($detail.id -ne $zone.id -or (Get-AsciiHost $detail.name) -ne $Domain -or
    $detail.account.id -ne $access.Account -or $detail.status -notin 'active','pending','initializing') {
    throw "Zone identity or status readback failed for $Domain."
}
$servers = @($detail.name_servers | ForEach-Object { Get-AsciiHost $_ })
if ($servers.Count -ne 2 -or $servers[0] -eq $servers[1] -or
    @($servers | Where-Object { $_ -notmatch '^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]+$' }).Count) {
    throw "Expected two distinct assigned nameservers for $Domain. Recheck the zone before delegation."
}
Write-Host "=== $Domain (네임서버 설정) ==="
Write-Host "  1차: $($servers[0])"
Write-Host "  2차: $($servers[1])"
