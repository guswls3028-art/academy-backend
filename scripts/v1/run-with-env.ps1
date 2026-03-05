# .env를 현재 프로세스에 환경변수로 넣은 뒤, 인자로 받은 명령을 실행한다.
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키를 환경변수로 넣어 배포·검증·인증을 진행한다. 본 스크립트는 .env를 읽어 자식 프로세스에 주입한다.
# 용도: 에이전트가 배포/인프라 명령을 "환경변수로 인증"된 상태에서 실행할 수 있게 함.
# 사용: pwsh -File scripts/v1/run-with-env.ps1 -- pwsh scripts/v1/deploy.ps1 -Env prod
#       pwsh -File scripts/v1/run-with-env.ps1 -- npx wrangler ...
$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
$envPath = Join-Path $RepoRoot ".env"
if (-not (Test-Path $envPath)) {
    Write-Error ".env not found at $envPath"
    exit 1
}
foreach ($line in (Get-Content -Path $envPath -Encoding UTF8 -ErrorAction SilentlyContinue)) {
    $t = $line.Trim()
    if ($t -match '^\s*#' -or $t -eq "") { continue }
    if ($t -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $k = $matches[1].Trim()
        $v = $matches[2].Trim() -replace '^"|"$', ''
        [Environment]::SetEnvironmentVariable($k, $v, "Process")
    }
}
$allArgs = @($args)
# Optional leading "--" (e.g. pwsh -File run-with-env.ps1 -- pwsh -File deploy.ps1)
if ($allArgs.Count -gt 0 -and $allArgs[0] -eq "--") {
    $allArgs = $allArgs[1..($allArgs.Count - 1)]
}
if ($allArgs.Count -eq 0) {
    Write-Error "Usage: pwsh -File run-with-env.ps1 [--] <command> [args...]"
    exit 1
}
$cmd = $allArgs[0]
$cmdArgs = @()
if ($allArgs.Count -gt 1) { $cmdArgs = $allArgs[1..($allArgs.Count - 1)] }
& $cmd @cmdArgs
exit $LASTEXITCODE
