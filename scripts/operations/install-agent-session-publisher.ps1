param(
    [ValidateSet('Install', 'Inspect')][string]$Action = 'Inspect',
    [string]$Revision,
    [string]$Python = 'C:\academy\backend\.venv\Scripts\pythonw.exe'
)
$ErrorActionPreference = 'Stop'
$taskName = 'Academy-AgentSessions-admin97'
$runtime = 'C:\academy\_runtime\agent-sessions'
$scriptPath = Join-Path $runtime 'agent_session_snapshot.py'
$statePath = Join-Path $runtime 'state.json'
$receiptPath = Join-Path $runtime 'installation.json'
$task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($Action -eq 'Inspect') {
    if ($task) {
        $info = Get-ScheduledTaskInfo -TaskName $taskName
        @{ task = $taskName; state = $task.State.ToString(); last_run = $info.LastRunTime.ToString('o'); last_result = $info.LastTaskResult } | ConvertTo-Json -Compress
    }
    if (Test-Path -LiteralPath $receiptPath) { Get-Content -LiteralPath $receiptPath }
    if (Test-Path -LiteralPath $statePath) { Get-Content -LiteralPath $statePath }
    exit 0
}
if ($Revision -notmatch '^[a-f0-9]{40}$') { throw 'Install requires the exact committed backend SHA.' }
if (-not (Test-Path -LiteralPath $Python)) { throw 'The existing Academy pythonw runtime is missing.' }
if ($task -and ($task.Description -notlike 'Academy read-only agent metadata publisher;*' -or $task.Actions.Arguments -notlike ('*' + $scriptPath + '*'))) {
    throw 'The task name belongs to another action; no changes made.'
}
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$start = [Diagnostics.ProcessStartInfo]::new()
$start.FileName = 'git'
$start.Arguments = '-C "' + $repo + '" show ' + $Revision + ':scripts/operations/agent_session_snapshot.py'
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$start.RedirectStandardOutput = $true
$start.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
$gitProcess = [Diagnostics.Process]::Start($start)
$source = $gitProcess.StandardOutput.ReadToEnd()
$gitProcess.WaitForExit()
if ($gitProcess.ExitCode -ne 0 -or -not $source) { throw 'Cannot read the committed collector.' }
$gitProcess.Dispose()
New-Item -ItemType Directory -Path $runtime -Force | Out-Null
if ($task) { Stop-ScheduledTask -TaskName $taskName }
# Task termination and the Python redirector's child exit are asynchronous.
# Wait for this publisher's mutex before replacing/restarting the pinned copy.
$guard = [Threading.Mutex]::new($false, 'Local\AcademyAgentSessions-admin97')
$acquired = $false
try {
    try { $acquired = $guard.WaitOne([TimeSpan]::FromSeconds(15)) }
    catch [Threading.AbandonedMutexException] { $acquired = $true }
    if (-not $acquired) { throw 'The existing publisher has not stopped; no replacement started.' }
    $guard.ReleaseMutex()
} finally { $guard.Dispose() }
[IO.File]::WriteAllText($scriptPath, $source, [Text.UTF8Encoding]::new($false))
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$arguments = '"' + $scriptPath + '" --publish --codex-home "C:\Users\heon7\.codex" --claude-home "C:\Users\heon7\.claude" --workspace "C:\academy" --state-path "' + $statePath + '"'
$taskAction = New-ScheduledTaskAction -Execute $Python -Argument $arguments -WorkingDirectory 'C:\academy'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $taskAction -Trigger $trigger -Principal $principal -Settings $settings -Description "Academy read-only agent metadata publisher; backend $Revision" -Force | Out-Null
$receipt = @{ revision = $Revision; sha256 = (Get-FileHash -LiteralPath $scriptPath -Algorithm SHA256).Hash; installed_at = [DateTime]::UtcNow.ToString('o'); task = $taskName }
[IO.File]::WriteAllText($receiptPath, ($receipt | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
Start-ScheduledTask -TaskName $taskName
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State
