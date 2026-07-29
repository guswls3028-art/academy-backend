# AWS/Cloudflare credentials are supplied by the caller through the intended profile or process environment; this script does not load backend/.env.
# Use the caller-provided profile or process environment; never print credential values.
$ErrorActionPreference = "Stop"
$script:EnvLoaded = $false

function Get-RepoRoot {
    $coreDir = $PSScriptRoot   # scripts/v1/core
    return (Resolve-Path (Join-Path $coreDir "..\..\..")).Path
}

function Load-EnvFile {
    param([string]$RepoRoot = (Get-RepoRoot))
    # Deprecated: 호출하지 말 것. 에이전트가 .env를 읽어 환경변수로 설정한 뒤 스크립트를 실행한다.
    if ($script:EnvLoaded) { return }
    $envPath = Join-Path $RepoRoot ".env"
    if (-not (Test-Path $envPath)) { return }
    $count = 0
    foreach ($line in (Get-Content -Path $envPath -Encoding UTF8 -ErrorAction SilentlyContinue)) {
        $trimmed = $line.Trim()
        if ($trimmed -match '^\s*#' -or $trimmed -eq "") { continue }
        if ($trimmed -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $key = $matches[1].Trim()
            $val = $matches[2].Trim()
            if ($val -match '^"(.*)"\s*$') { $val = $matches[1] }
            [Environment]::SetEnvironmentVariable($key, $val, "Process")
            $count++
        }
    }
    $script:EnvLoaded = $true
    if ($count -gt 0) {
        Write-Host "  .env loaded ($count vars) from $envPath" -ForegroundColor DarkGray
    }
}

function Assert-AwsCredentials {
    param([string]$RepoRoot = (Get-RepoRoot))
    # .env 로드 없이 현재 프로세스 환경변수만으로 검증 (에이전트가 이미 설정한 값 사용)
    $region = $env:AWS_DEFAULT_REGION
    if (-not $region) { $region = $env:AWS_REGION }
    if (-not $region) { $region = "ap-northeast-2" }
    $profileArgs = @()
    if ($env:AWS_PROFILE -and $env:AWS_PROFILE.Trim() -ne "") {
        $profileArgs = @("--profile", $env:AWS_PROFILE.Trim())
    }
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & aws sts get-caller-identity --output json --region $region @profileArgs 2>&1
        $exit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($exit -ne 0 -or -not $out) {
        $msg = @"
AWS 자격 증명이 없거나 만료되었습니다.
  - 호출 전에 루트 .env의 AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION 을 환경변수로 설정해 주세요.
  - 로컬 수동 배포: `-AwsProfile default` 또는 의도한 process environment를 사용합니다.
  - 프로파일 사용 시: -AwsProfile <이름> 으로 실행하세요.
"@
        throw $msg
    }
    $id = $out | ConvertFrom-Json
    return $id
}

function Assert-AwsMutationIdentity {
    param(
        [object]$Identity = $null,
        [string]$RepoRoot = (Get-RepoRoot)
    )
    $id = if ($Identity) { $Identity } else { Assert-AwsCredentials -RepoRoot $RepoRoot }
    $arn = [string]$id.Arn
    if (-not $arn) {
        throw "AWS mutation identity is missing an ARN."
    }
    if ($arn -match '^arn:aws:iam::[0-9]{12}:root$') {
        throw (
            "AWS mutation is blocked for account root credentials. " +
            "Use the GitHub OIDC deployment role or a dedicated least-privilege AWS profile."
        )
    }
    return $id
}
