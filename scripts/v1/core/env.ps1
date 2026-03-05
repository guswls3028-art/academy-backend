# .env 는 스크립트에서 로드하지 않음. Cursor(에이전트)가 루트 .env를 열람해 환경변수로 설정한 뒤 스크립트를 실행한다.
# 이 파일은 Assert-AwsCredentials 등 공통 함수만 제공. Load-EnvFile 은 사용하지 않음.
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
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        $out = & aws sts get-caller-identity --output json --region $region 2>&1
        $exit = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($exit -ne 0 -or -not $out) {
        $msg = @"
AWS 자격 증명이 없거나 만료되었습니다.
  - .env에 AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION 이 설정되어 있는지 확인하세요.
  - 프로젝트 루트의 .env 파일이 있는지 확인하세요. (계정 루트가 아닌 academy 리포 루트)
  - 프로파일 사용 시: -AwsProfile <이름> 으로 실행하세요.
"@
        throw $msg
    }
    $id = $out | ConvertFrom-Json
    return $id
}
