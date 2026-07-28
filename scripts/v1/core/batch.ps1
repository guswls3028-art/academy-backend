# AWS Batch container resource compatibility helpers.
$ErrorActionPreference = "Stop"

function Get-BatchContainerResourceValue {
    param(
        $ContainerProperties,
        [ValidateSet("VCPU", "MEMORY")]
        [string]$Type
    )

    if (-not $ContainerProperties) { return $null }

    $resource = @($ContainerProperties.resourceRequirements) |
        Where-Object { [string]$_.type -eq $Type } |
        Select-Object -First 1
    if ($resource -and $null -ne $resource.value -and [string]$resource.value -ne "") {
        return [int]$resource.value
    }

    $legacyProperty = if ($Type -eq "VCPU") { "vcpus" } else { "memory" }
    $legacyValue = $ContainerProperties.$legacyProperty
    if ($null -eq $legacyValue -or [string]$legacyValue -eq "") { return $null }
    return [int]$legacyValue
}
