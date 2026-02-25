# SSM JSON 직렬화 검증 — 복붙 테스트 커맨드

저장소 루트에서 PowerShell로 실행. UTF-8 설정 후 순서대로 실행.

## 1) SSM bootstrap 실행

```powershell
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
.\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region ap-northeast-2 -EnvFile .env -Overwrite
```

기대: `OK: /academy/workers/env written successfully. Stored value validated as valid JSON with DJANGO_SETTINGS_MODULE=worker.`

---

## 2) get-parameter로 값 가져오기 (값 자체는 출력하지 않음, 파싱만 검증)

```powershell
$raw = aws ssm get-parameter --name "/academy/workers/env" --region ap-northeast-2 --with-decryption --output json 2>&1
$responseStr = ($raw | Out-String).Trim()
$outer = $responseStr | ConvertFrom-Json
$valueStr = $outer.Parameter.Value
Write-Host "Parameter.Value length: $($valueStr.Length) chars" -ForegroundColor Cyan
```

기대: 길이 출력, 에러 없음.

---

## 3) ConvertFrom-Json 테스트 (저장된 값이 유효한 JSON인지)

```powershell
$payload = $valueStr | ConvertFrom-Json
$payload.PSObject.Properties.Name | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
if ($payload.DJANGO_SETTINGS_MODULE -eq "apps.api.config.settings.worker") { Write-Host "OK: DJANGO_SETTINGS_MODULE=worker" -ForegroundColor Green } else { Write-Host "FAIL: DJANGO_SETTINGS_MODULE missing or wrong" -ForegroundColor Red }
```

기대: 키 목록 출력, `OK: DJANGO_SETTINGS_MODULE=worker`.

---

## 4) verify_ssm_env_shape (동일 파싱 로직)

```powershell
.\scripts\infra\verify_ssm_env_shape.ps1 -Region ap-northeast-2
```

기대: `OK: SSM parameter exists, valid JSON, all required keys present and non-empty, DJANGO_SETTINGS_MODULE=worker.`

---

## 5) Netprobe job (큐 이름은 batch_final_state.json 또는 기본값)

```powershell
$q = "academy-video-batch-queue"
if (Test-Path (Join-Path $PWD "docs\deploy\actual_state\batch_final_state.json")) {
    $q = (Get-Content (Join-Path $PWD "docs\deploy\actual_state\batch_final_state.json") -Raw | ConvertFrom-Json).FinalJobQueueName
}
.\scripts\infra\run_netprobe_job.ps1 -Region ap-northeast-2 -JobQueueName $q
```

기대: `SUCCEEDED` 및 로그.

---

## 6) Production done check

```powershell
.\scripts\infra\production_done_check.ps1 -Region ap-northeast-2
```

기대: `PRODUCTION DONE CHECK: PASS`.

---

## 한 번에 복붙 (2·3은 위에서 이미 $valueStr, $payload 사용 시)

```powershell
# UTF-8
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

# 1) SSM bootstrap
.\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region ap-northeast-2 -EnvFile .env -Overwrite

# 2) get-parameter + 3) ConvertFrom-Json 테스트
$raw = aws ssm get-parameter --name "/academy/workers/env" --region ap-northeast-2 --with-decryption --output json 2>&1
$responseStr = ($raw | Out-String).Trim()
$outer = $responseStr | ConvertFrom-Json
$valueStr = $outer.Parameter.Value
Write-Host "Parameter.Value length: $($valueStr.Length) chars" -ForegroundColor Cyan
$payload = $valueStr | ConvertFrom-Json
$payload.PSObject.Properties.Name | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
if ($payload.DJANGO_SETTINGS_MODULE -eq "apps.api.config.settings.worker") { Write-Host "OK: DJANGO_SETTINGS_MODULE=worker" -ForegroundColor Green } else { Write-Host "FAIL: DJANGO_SETTINGS_MODULE missing or wrong" -ForegroundColor Red }

# 4) verify
.\scripts\infra\verify_ssm_env_shape.ps1 -Region ap-northeast-2

# 5) Netprobe
$q = "academy-video-batch-queue"
if (Test-Path (Join-Path $PWD "docs\deploy\actual_state\batch_final_state.json")) { $q = (Get-Content (Join-Path $PWD "docs\deploy\actual_state\batch_final_state.json") -Raw | ConvertFrom-Json).FinalJobQueueName }
.\scripts\infra\run_netprobe_job.ps1 -Region ap-northeast-2 -JobQueueName $q

# 6) Production done check
.\scripts\infra\production_done_check.ps1 -Region ap-northeast-2
```
