[CmdletBinding()]
param(
    [string]$Sha = "",
    [string]$AwsProfile = "default",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
& (Join-Path $PSScriptRoot "rollback-asg.ps1") -Service api -ImageTag $Sha -AwsProfile $AwsProfile -WhatIf:$WhatIf
exit $LASTEXITCODE
