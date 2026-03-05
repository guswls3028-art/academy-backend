# .env 자동 로드 및 AWS 자격 증명 검증. 모든 v1 스크립트에서 공통 사용.
# .env 형식: KEY=value (한 줄씩). # 주석·빈 줄 무시. PowerShell $env: 문법 사용하지 않음.
$ErrorActionPreference = "Stop"
$script:EnvLoaded = $false

function Get-RepoRoot {
    $coreDir = $PSScriptRoot   # scripts/v1/core
    return (Resolve-Path (Join-Path $coreDir "..\..\..")).Path
}

function Load-EnvFile {
    param([string]$RepoRoot = (Get-RepoRoot))
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
    if ($count -gt 0) {
        Write-Host "  .env loaded ($count vars) from $envPath" -ForegroundColor DarkGray
    }
}

function Assert-AwsCredentials {
    param([string]$RepoRoot = (Get-RepoRoot))
    Load-EnvFile -RepoRoot $RepoRoot | Out-Null
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
