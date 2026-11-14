# Pure launch-payload builder. Never changes an existing instance or volume.
function Get-ApiDevelopmentRootBlockDeviceMapping {
    param(
        [Parameter(Mandatory = $true)]$Image,
        [Parameter(Mandatory = $true)][ValidateRange(16, 1024)][int]$VolumeSizeGb
    )
    $rootName = [string]$Image.RootDeviceName
    if ($Image.RootDeviceType -ne "ebs" -or $rootName -notmatch '^/dev/[A-Za-z0-9._/-]+$') {
        throw "Development AMI must have a named EBS root device."
    }
    $root = @($Image.BlockDeviceMappings | Where-Object { $_.DeviceName -eq $rootName })
    if ($root.Count -ne 1 -or -not $root[0].Ebs -or [int]$root[0].Ebs.VolumeSize -le 0) {
        throw "Development AMI must have exactly one sized EBS root mapping."
    }
    if ($VolumeSizeGb -lt [int]$root[0].Ebs.VolumeSize) {
        throw "Development root volume cannot be smaller than the AMI root volume."
    }
    # RunInstances merges this override into the AMI mapping. Omit snapshot,
    # encryption/KMS, type and performance fields to retain their existing defaults.
    return [ordered]@{
        DeviceName = $rootName
        Ebs = [ordered]@{ VolumeSize = $VolumeSizeGb; DeleteOnTermination = $true }
    }
}
