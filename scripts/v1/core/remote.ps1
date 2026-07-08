# Shared SSM helpers for running shell commands on live API instances/containers.

function Invoke-ApiSsmShellCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [int]$TimeoutSec = 180,
        [switch]$AllInstances
    )

    $ids = @(Get-APIASGInstanceIds | Where-Object { $_ -and $_.Trim() -ne "" })
    if (-not $ids -or $ids.Count -eq 0) {
        return @([PSCustomObject]@{
            InstanceId = ""
            Status = "Failed"
            ResponseCode = 1
            StandardOutputContent = ""
            StandardErrorContent = "No API ASG instances found"
            CommandId = ""
        })
    }

    if (-not $AllInstances) {
        $ids = @($ids[0])
    }

    $results = [System.Collections.ArrayList]::new()
    foreach ($instanceId in $ids) {
        $normalizedCommand = $Command -replace "`r`n", "`n" -replace "`r", "`n"
        $paramsJson = @{ commands = @($normalizedCommand) } | ConvertTo-Json -Compress
        $paramsArg = $paramsJson
        if (Get-Command Convert-JsonArgToFileRef -ErrorAction SilentlyContinue) {
            $paramsArg = Convert-JsonArgToFileRef $paramsJson
        }

        try {
            $send = Invoke-AwsJson @(
                "ssm", "send-command",
                "--instance-ids", $instanceId,
                "--document-name", "AWS-RunShellScript",
                "--parameters", $paramsArg,
                "--region", $script:Region,
                "--output", "json"
            )
        } finally {
            if ($paramsArg -like "file://*") {
                $tmp = $paramsArg.Substring(7)
                Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue
            }
        }

        $commandId = ""
        if ($send -and $send.Command -and $send.Command.CommandId) {
            $commandId = [string]$send.Command.CommandId
        }
        if (-not $commandId) {
            [void]$results.Add([PSCustomObject]@{
                InstanceId = $instanceId
                Status = "Failed"
                ResponseCode = 1
                StandardOutputContent = ""
                StandardErrorContent = "SSM send-command failed"
                CommandId = ""
            })
            continue
        }

        $elapsed = 0
        $pollSec = 5
        $final = $null
        while ($elapsed -lt $TimeoutSec) {
            Start-Sleep -Seconds $pollSec
            $elapsed += $pollSec
            $inv = Invoke-AwsJson @(
                "ssm", "get-command-invocation",
                "--command-id", $commandId,
                "--instance-id", $instanceId,
                "--region", $script:Region,
                "--output", "json"
            )
            if (-not $inv) {
                continue
            }
            if ($inv.Status -in @("Success", "Failed", "Cancelled", "TimedOut")) {
                $final = $inv
                break
            }
        }

        if (-not $final) {
            [void]$results.Add([PSCustomObject]@{
                InstanceId = $instanceId
                Status = "TimedOut"
                ResponseCode = 124
                StandardOutputContent = ""
                StandardErrorContent = "Timed out after ${TimeoutSec}s"
                CommandId = $commandId
            })
            continue
        }

        [void]$results.Add([PSCustomObject]@{
            InstanceId = $instanceId
            Status = [string]$final.Status
            ResponseCode = [int]$final.ResponseCode
            StandardOutputContent = [string]$final.StandardOutputContent
            StandardErrorContent = [string]$final.StandardErrorContent
            CommandId = $commandId
        })
    }

    return @($results)
}

function Invoke-ApiSsmDockerExec {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [int]$TimeoutSec = 180,
        [switch]$AllInstances
    )

    $containerName = if ($script:ApiContainerName) { $script:ApiContainerName } else { "academy-api" }
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Command))
    $remoteCommand = @"
container='$containerName'
for i in `$(seq 1 24); do
  state=`$(sudo docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "`$container" 2>/dev/null || true)
  if [ "`$state" = "running healthy" ] || [ "`$state" = "running none" ]; then
    break
  fi
  sleep 5
done
state=`$(sudo docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "`$container" 2>/dev/null || true)
if [ "`$state" != "running healthy" ] && [ "`$state" != "running none" ]; then
  echo "container `$container not ready: `$state" >&2
  sudo docker ps -a >&2 || true
  exit 125
fi
printf '%s' '$encoded' | base64 -d | sudo docker exec -i "`$container" sh 2>&1
"@
    return Invoke-ApiSsmShellCommand -Command $remoteCommand -TimeoutSec $TimeoutSec -AllInstances:$AllInstances
}
