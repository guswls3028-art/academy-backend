function Test-YmathLoginUatNumericZero {
    param([object]$Value)

    if ($null -eq $Value) { return $false }
    $numericTypes = @(
        [byte], [sbyte], [int16], [uint16], [int32], [uint32],
        [int64], [uint64], [single], [double], [decimal]
    )
    if (-not ($numericTypes | Where-Object { $Value -is $_ })) { return $false }
    return [decimal]$Value -eq 0
}

function Assert-YmathLoginUatCleanupPayload {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Payload,
        [Parameter(Mandatory = $true)]
        [string]$TenantCode
    )

    $validStatus = $Payload.status -in @(
        "YMATH_REALUSE_SCENARIO_DESTROYED",
        "YMATH_REALUSE_SCENARIO_ABSENT"
    )
    $remainingProperty = $Payload.PSObject.Properties["remaining"]
    $remaining = if ($remainingProperty) { $remainingProperty.Value } else { $null }
    $tenantsProperty = if ($null -ne $remaining) { $remaining.PSObject.Properties["tenants"] } else { $null }
    $usersProperty = if ($null -ne $remaining) { $remaining.PSObject.Properties["users"] } else { $null }
    $tenants = if ($tenantsProperty) { $tenantsProperty.Value } else { $null }
    $users = if ($usersProperty) { $usersProperty.Value } else { $null }

    if (
        -not $validStatus -or
        [string]$Payload.tenant_code -cne $TenantCode -or
        $null -eq $remainingProperty -or
        $null -eq $remaining -or
        $null -eq $tenantsProperty -or
        $null -eq $usersProperty -or
        -not (Test-YmathLoginUatNumericZero -Value $tenants) -or
        -not (Test-YmathLoginUatNumericZero -Value $users)
    ) {
        throw "Persistent-development cleanup did not prove exact tenant/user zero residue."
    }

    return $Payload
}
