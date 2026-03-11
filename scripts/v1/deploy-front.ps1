# 프론트 정적 빌드 → R2 업로드 → CDN purge → 검증
# AWS·Cloudflare(클플) 인증: Cursor 룰(.cursor/rules)에 의거 .env 직접 열람 후 키를 환경변수로 넣어 배포·검증·인증을 진행한다. 스크립트는 .env를 로드하지 않는다.
# deploy.ps1 -DeployFront 시 호출. .env에서 CLOUDFLARE_* 로드 후 실행 권장 (run-with-env.ps1).
# SSOT: docs/00-SSOT/v1/params.yaml front.*
$ErrorActionPreference = "Stop"
param(
    [string]$RepoRoot = "",
    [string]$FrontRepoPath = "",  # 비어 있으면 $RepoRoot\..\academyfront
    [switch]$SkipBuild = $false,
    [switch]$SkipUpload = $false,
    [switch]$SkipPurge = $false,
    [switch]$DryRun = $false
)
$ScriptRoot = $PSScriptRoot
if (-not $RepoRoot) { $RepoRoot = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path }
$ParamsPath = Join-Path $RepoRoot "docs\00-SSOT\v1\params.yaml"
if (-not (Test-Path $ParamsPath)) {
    Write-Host "params.yaml not found: $ParamsPath" -ForegroundColor Yellow
    exit 0
}
if (-not $FrontRepoPath) { $FrontRepoPath = Join-Path (Split-Path $RepoRoot -Parent) "academyfront" }
if (-not (Test-Path $FrontRepoPath)) {
    Write-Host "Front repo not found: $FrontRepoPath. Set -FrontRepoPath or place academyfront next to academy." -ForegroundColor Yellow
    exit 0
}

# params front 섹션 간단 파싱 (키:값 2단계만)
$front = @{}
$inFront = $false
foreach ($line in (Get-Content $ParamsPath -Raw) -split "`r?`n") {
    if ($line -match '^front:\s*$') { $inFront = $true; continue }
    if ($inFront -and $line -match '^\s{2}([a-zA-Z0-9_]+):\s*(.*)$') {
        $front[$matches[1]] = $matches[2].Trim().Trim('"')
        continue
    }
    if ($inFront -and $line -match '^\w' -and $line -notmatch '^\s') { $inFront = $false }
}
$buildOutputDir = if ($front["buildOutputDir"]) { $front["buildOutputDir"] } else { "dist" }
$r2Bucket = if ($front["r2StaticBucket"]) { $front["r2StaticBucket"] } else { "" }
$r2Prefix = if ($front["r2StaticPrefix"]) { $front["r2StaticPrefix"] } else { "static/front" }
$purgeOnDeploy = ($front["purgeOnDeploy"] -eq "true")

Write-Host "`n=== Front Deploy (SSOT: params front.*) ===" -ForegroundColor Cyan
Write-Host "  FrontRepoPath: $FrontRepoPath" -ForegroundColor Gray
Write-Host "  buildOutputDir: $buildOutputDir" -ForegroundColor Gray
Write-Host "  r2StaticBucket: $r2Bucket" -ForegroundColor Gray
Write-Host "  r2StaticPrefix: $r2Prefix" -ForegroundColor Gray

if ($DryRun) {
    Write-Host "  DryRun: no changes." -ForegroundColor Yellow
    exit 0
}

# 1) Build
if (-not $SkipBuild) {
    $distPath = Join-Path $FrontRepoPath $buildOutputDir
    if (-not (Test-Path $FrontRepoPath)) { Write-Host "  [Front] Skip build: path not found." -ForegroundColor Yellow }
    else {
        Push-Location $FrontRepoPath
        try {
            if (Get-Command pnpm -ErrorAction SilentlyContinue) {
                pnpm run build 2>&1 | Out-Host
            } elseif (Get-Command npm -ErrorAction SilentlyContinue) {
                npm run build 2>&1 | Out-Host
            } else {
                Write-Host "  [Front] pnpm/npm not found; skip build. Put pre-built files in $distPath" -ForegroundColor Yellow
            }
        } finally { Pop-Location }
    }
} else {
    Write-Host "  [Front] Skip build (-SkipBuild)." -ForegroundColor Gray
}

# 2) Upload to R2 (wrangler r2 object put)
if (-not $SkipUpload -and $r2Bucket) {
    $distPath = Join-Path $FrontRepoPath $buildOutputDir
    if (-not (Test-Path $distPath)) {
        Write-Host "  [Front] dist not found: $distPath" -ForegroundColor Yellow
    } else {
        $files = Get-ChildItem -Path $distPath -Recurse -File
        foreach ($f in $files) {
            $rel = $f.FullName.Substring($distPath.Length).TrimStart('\', '/')
            $key = "$r2Prefix/$rel" -replace '\\', '/'
            try {
                npx wrangler r2 object put "$r2Bucket/$key" --file $f.FullName 2>&1 | Out-Host
            } catch {
                Write-Host "  [Front] wrangler r2 put failed for $key : $_" -ForegroundColor Yellow
            }
        }
        Write-Host "  [Front] R2 upload done: $r2Bucket / $r2Prefix" -ForegroundColor Green
    }
} else {
    if (-not $r2Bucket) { Write-Host "  [Front] Skip upload: r2StaticBucket not set in params." -ForegroundColor Gray }
    else { Write-Host "  [Front] Skip upload (-SkipUpload)." -ForegroundColor Gray }
}

# 3) CDN purge (Cloudflare API)
if (-not $SkipPurge -and $purgeOnDeploy) {
    $zoneId = $env:CLOUDFLARE_ZONE_ID
    if ($zoneId) {
        try {
            $headers = @{
                "X-Auth-Email" = $env:CLOUDFLARE_EMAIL
                "X-Auth-Key"   = $env:CLOUDFLARE_API_KEY
                "Content-Type" = "application/json"
            }
            $body = '{"purge_everything":true}' | ConvertTo-Json -Compress
            Invoke-RestMethod -Uri "https://api.cloudflare.com/client/v4/zones/$zoneId/purge_cache" -Method Post -Headers $headers -Body $body -ContentType "application/json"
            Write-Host "  [Front] Cloudflare cache purge done." -ForegroundColor Green
        } catch {
            Write-Host "  [Front] Purge failed (set CLOUDFLARE_ZONE_ID, CLOUDFLARE_EMAIL, CLOUDFLARE_API_KEY): $_" -ForegroundColor Yellow
        }
    } else {
        Write-Host "  [Front] Skip purge: CLOUDFLARE_ZONE_ID not set." -ForegroundColor Gray
    }
} else {
    Write-Host "  [Front] Skip purge." -ForegroundColor Gray
}

# 4) Verification (optional: curl app domain)
$appUrl = $env:FRONT_APP_URL
if ($appUrl) {
    try {
        $r = Invoke-WebRequest -Uri $appUrl -UseBasicParsing -TimeoutSec 10
        if ($r.StatusCode -eq 200) { Write-Host "  [Front] Verification: $appUrl -> $($r.StatusCode)" -ForegroundColor Green }
        else { Write-Host "  [Front] Verification: $appUrl -> $($r.StatusCode)" -ForegroundColor Yellow }
    } catch {
        Write-Host "  [Front] Verification failed: $appUrl : $_" -ForegroundColor Yellow
    }
}
Write-Host "=== Front Deploy done ===`n" -ForegroundColor Cyan
