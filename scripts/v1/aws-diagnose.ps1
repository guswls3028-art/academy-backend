# AWS credential/region diagnostic — run in the same environment as deploy.ps1 (e.g. Cursor Run Command).
# Usage: pwsh scripts/v1/aws-diagnose.ps1
$ErrorActionPreference = "Continue"
$R = "ap-northeast-2"

Write-Host "`n=== AWS credential diagnostic ===" -ForegroundColor Cyan
Write-Host ""

# 1) Credential source
Write-Host "1) aws configure list" -ForegroundColor Yellow
try { aws configure list 2>&1 } catch { Write-Host $_ }
Write-Host ""

# 2) Profiles
Write-Host "2) aws configure list-profiles" -ForegroundColor Yellow
try { aws configure list-profiles 2>&1 } catch { Write-Host $_ }
Write-Host ""

# 3) Default region
Write-Host "3) aws configure get region" -ForegroundColor Yellow
try { $r = aws configure get region 2>&1; if ($r) { Write-Host $r } else { Write-Host "(not set)" } } catch { Write-Host $_ }
Write-Host ""

# 4) Env vars (mask secrets)
Write-Host "4) Environment variables" -ForegroundColor Yellow
$ak = $env:AWS_ACCESS_KEY_ID; $sk = $env:AWS_SECRET_ACCESS_KEY; $st = $env:AWS_SESSION_TOKEN
Write-Host "  AWS_ACCESS_KEY_ID: $(if ($ak) { $ak.Substring(0, [Math]::Min(4, $ak.Length)) + '...' } else { '(not set)' })"
Write-Host "  AWS_SECRET_ACCESS_KEY: $(if ($sk) { '****' } else { '(not set)' })"
Write-Host "  AWS_SESSION_TOKEN: $(if ($st) { '****' } else { '(not set)' })"
Write-Host "  AWS_PROFILE: $($env:AWS_PROFILE ?? '(not set)')"
Write-Host "  AWS_DEFAULT_REGION: $($env:AWS_DEFAULT_REGION ?? '(not set)')"
Write-Host "  AWS_REGION: $($env:AWS_REGION ?? '(not set)')"
Write-Host ""

# 5) sts get-caller-identity (exact error)
Write-Host "5) aws sts get-caller-identity --region $R" -ForegroundColor Yellow
$out = aws sts get-caller-identity --region $R 2>&1
$exit = $LASTEXITCODE
if ($exit -eq 0) {
    Write-Host $out -ForegroundColor Green
    Write-Host "  -> OK: credentials valid in this process." -ForegroundColor Green
} else {
    Write-Host $out -ForegroundColor Red
    Write-Host "  -> FAIL: ExitCode=$exit. This is the same error deploy.ps1 sees." -ForegroundColor Red
    if ($out -match "InvalidClientTokenId") { Write-Host "  Cause: token invalid or expired (env vars / profile)." -ForegroundColor Yellow }
    if ($out -match "Unable to locate credentials") { Write-Host "  Cause: no credentials in this process (set env or use -AwsProfile)." -ForegroundColor Yellow }
    if ($out -match "AccessDenied") { Write-Host "  Cause: IAM permissions (sts:GetCallerIdentity allowed?)." -ForegroundColor Yellow }
}
Write-Host ""

Write-Host "=== End diagnostic ===" -ForegroundColor Cyan
Write-Host "Fix: use default profile (aws configure) or run: pwsh scripts/v1/deploy.ps1 -Env prod -AwsProfile <profile>" -ForegroundColor Gray
Write-Host ""
