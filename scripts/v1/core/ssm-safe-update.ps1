# ==============================================================================
# SSM SecureString safe-update helper — Python b64 round-trip 강제.
# ==============================================================================
# 2026-05-12 사고: PowerShell `ConvertFrom-Json` / `ConvertTo-Json` 가 SSM
# `/academy/api/env` 60-key 객체를 LIST 로 변환해 SecureString 을 손상시킴
# (Version 30, 31). 결과적으로 API 컨테이너가 SECRET_KEY/DB_PASSWORD 등 60
# 키를 잃은 채 부팅 시도 → ImproperlyConfigured. 복구는 EC2 측 docker inspect
# env 추출 + Python JSON 재조립 + base64 wrapper + 로컬 PUT (Version 32).
#
# 본 helper 는 위 사고 재발 방지용. PowerShell 측 JSON 직조작 없이
# Python 한 곳에 round-trip 을 가두고, PUT 전 invariant 검증을 강제.
#
# 두 가지 SSM 포맷을 모두 지원:
#   - plain JSON       (e.g. `/academy/api/env`)
#   - base64 wrapped JSON (e.g. `/academy/workers/env`)
#
# 사용 예:
#   . scripts/v1/core/ssm-safe-update.ps1
#   Update-AcademySSMParameter `
#     -Name '/academy/api/env' `
#     -KeyUpdates @{ 'SECRET_KEY' = $newSk; 'INTERNAL_WORKER_TOKEN' = $newIwt } `
#     -ExpectMinKeys 50 `
#     -Wrapping 'plain'
#
#   Update-AcademySSMParameter `
#     -Name '/academy/workers/env' `
#     -KeyUpdates @{ 'SECRET_KEY' = $newSk } `
#     -ExpectMinKeys 40 `
#     -Wrapping 'base64'
# ==============================================================================

function Update-AcademySSMParameter {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [hashtable]$KeyUpdates,

        # 안전장치: PUT 전 JSON 디코드 후 키 수가 이 값 미만이면 abort.
        # plain api/env 는 ~60, b64 workers/env 는 ~43.
        [Parameter(Mandatory = $true)]
        [int]$ExpectMinKeys,

        # 'plain' = SSM 에 JSON 그대로 / 'base64' = base64 wrapped JSON
        [ValidateSet('plain', 'base64')]
        [string]$Wrapping = 'plain',

        [string]$Region = 'ap-northeast-2',

        # 백업 디렉토리 (기본: c:\academy\_artifacts)
        [string]$BackupDir = 'C:\academy\_artifacts'
    )

    if (-not (Test-Path $BackupDir)) {
        New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    }
    $ts = Get-Date -Format 'yyyyMMdd_HHmmss'
    $safeName = ($Name -replace '[^A-Za-z0-9]', '_').Trim('_')
    $beforePath = Join-Path $BackupDir "_ssm_${safeName}_before_${ts}.txt"
    $afterPath  = Join-Path $BackupDir "_ssm_${safeName}_after_${ts}.json"
    $afterB64Path = Join-Path $BackupDir "_ssm_${safeName}_after_${ts}.b64"

    Write-Host "[SSM-SAFE] Pulling current value of $Name ..." -ForegroundColor Cyan
    $raw = aws ssm get-parameter --name $Name --with-decryption --query 'Parameter.Value' --output text --region $Region 2>&1
    if (-not $raw -or "$raw" -match '^An error|^usage:|ParameterNotFound') {
        throw "[SSM-SAFE] Failed to read $Name : $raw"
    }
    # Save before (raw) for rollback evidence
    $raw | Out-File -Encoding ascii -NoNewline $beforePath
    Write-Host "[SSM-SAFE]   raw size = $($raw.Length) bytes, backup -> $beforePath" -ForegroundColor Gray

    # Build Python script to: decode (b64 or plain) -> validate dict -> apply updates -> validate min keys -> emit
    $pyScript = @'
import sys, json, base64, os
raw = open(sys.argv[1], 'r', encoding='ascii').read().strip()
wrap = sys.argv[2]
updates_path = sys.argv[3]
out_json_path = sys.argv[4]
out_b64_path = sys.argv[5]
expect_min = int(sys.argv[6])

if wrap == 'base64':
    decoded = base64.b64decode(raw).decode('utf-8')
else:
    decoded = raw

try:
    d = json.loads(decoded)
except Exception as e:
    print(f"DECODE_FAIL: {e}", file=sys.stderr)
    sys.exit(2)

if not isinstance(d, dict):
    print(f"NOT_DICT: decoded value is {type(d).__name__}, expected dict", file=sys.stderr)
    sys.exit(3)

orig_keys = len(d)
if orig_keys < expect_min:
    print(f"INSUFFICIENT_KEYS: got {orig_keys}, expected >= {expect_min}", file=sys.stderr)
    sys.exit(4)

# Apply updates
updates = json.load(open(updates_path, 'r', encoding='utf-8'))
applied = []
for k, v in updates.items():
    old_len = len(str(d.get(k, '')))
    new_len = len(str(v))
    d[k] = v
    applied.append(f"{k}: {old_len} -> {new_len}")

if len(d) < expect_min:
    print(f"POST_INSUFFICIENT_KEYS: got {len(d)}, expected >= {expect_min}", file=sys.stderr)
    sys.exit(5)

# Emit
new_json = json.dumps(d, separators=(',', ':'))
open(out_json_path, 'w', encoding='utf-8').write(new_json)
if wrap == 'base64':
    new_b64 = base64.b64encode(new_json.encode('utf-8')).decode('ascii')
    open(out_b64_path, 'w', encoding='ascii').write(new_b64)
    print(f"OK keys={len(d)} json_size={len(new_json)} b64_size={len(new_b64)}")
else:
    print(f"OK keys={len(d)} json_size={len(new_json)}")
print("APPLIED " + " | ".join(applied))
'@
    $pyScriptPath = Join-Path $BackupDir "_ssm_safe_update_$ts.py"
    $pyScript | Out-File -Encoding utf8 -NoNewline $pyScriptPath

    # Write KeyUpdates as JSON file for Python to read (avoids quoting hell)
    $updatesPath = Join-Path $BackupDir "_ssm_updates_$ts.json"
    ($KeyUpdates | ConvertTo-Json -Compress) | Out-File -Encoding utf8 -NoNewline $updatesPath

    Write-Host "[SSM-SAFE] Running Python validator/transformer ..." -ForegroundColor Cyan
    $pyOut = & python $pyScriptPath $beforePath $Wrapping $updatesPath $afterPath $afterB64Path $ExpectMinKeys 2>&1
    $pyExit = $LASTEXITCODE
    if ($pyExit -ne 0) {
        Write-Host "[SSM-SAFE] Python FAIL (exit $pyExit):" -ForegroundColor Red
        Write-Host $pyOut -ForegroundColor Red
        Write-Host "[SSM-SAFE] ABORT. SSM NOT updated. Before-value preserved at $beforePath" -ForegroundColor Red
        throw "[SSM-SAFE] aborted"
    }
    Write-Host "[SSM-SAFE] $pyOut" -ForegroundColor Gray

    $putValuePath = if ($Wrapping -eq 'base64') { $afterB64Path } else { $afterPath }
    Write-Host "[SSM-SAFE] PUT $Name (from $putValuePath) ..." -ForegroundColor Cyan
    $putOut = aws ssm put-parameter --name $Name --type SecureString --value "file://$putValuePath" --overwrite --region $Region --query Version --output text 2>&1
    if (-not $putOut -or "$putOut" -match '^An error') {
        throw "[SSM-SAFE] PUT failed: $putOut"
    }
    Write-Host "[SSM-SAFE] OK new SSM version = $putOut" -ForegroundColor Green
    return [int]$putOut
}

# Sanity smoke when sourced
if ($MyInvocation.InvocationName -eq '.' -or $MyInvocation.InvocationName -eq '&') {
    Write-Host "Update-AcademySSMParameter ready. Wrappings: plain | base64." -ForegroundColor DarkGray
}
