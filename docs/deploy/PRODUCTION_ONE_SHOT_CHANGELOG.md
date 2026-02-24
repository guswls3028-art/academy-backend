# Production One-Shot: 변경 목록 및 원테이크 실행

## 변경된 파일 목록 및 요약

### A) 런타임 부트 경로 단일화

| 파일 | 변경 요약 |
|------|-----------|
| `apps/worker/video_worker/batch_entrypoint.py` | SSM 값을 JSON만 파싱(KEY=VAL 폴백 제거). REQUIRED_KEYS 검사 후 env 설정. `DJANGO_SETTINGS_MODULE`가 없거나 `apps.api.config.settings.worker`가 아니면 즉시 exit 1. |
| `manage.py` | `setdefault("DJANGO_SETTINGS_MODULE", "apps.api.config.settings.dev")` 제거. 미설정 시 에러 메시지 출력 후 exit 1. |
| `apps/worker/video_worker/batch_main.py` | `DJANGO_SETTINGS_MODULE` setdefault 제거. 미설정 시 "Run via batch_entrypoint" 메시지 후 exit 1. |
| `docs/deploy/SSM_JSON_SCHEMA.md` | SSM JSON 스키마(필수/선택 키, 런타임 계약) 문서화. |

### B) SSM JSON 표준화 + 검증 툴

| 파일 | 변경 요약 |
|------|-----------|
| `scripts/infra/verify_ssm_env_shape.ps1` | `--with-decryption`으로 읽은 전체 응답을 문자열로 합친 뒤 JSON 파싱. 필수 키·비어 있지 않음·`DJANGO_SETTINGS_MODULE=worker` 검사. 실패 시 exit 1. |
| `scripts/infra/verify_batch_network_connectivity.ps1` | 상단 UTF-8 인코딩 설정. SSM 전체 문자열 파싱, ECS cluster ARN 추출·null 처리. |
| `scripts/infra/production_done_check.ps1` | ExecJson에서 AWS CLI 출력을 `Out-String`으로 합쳐 ConvertFrom-Json. netprobe FAILED 시 수정 가이드 출력. 최종 FAIL 시 "Resolve the FAIL lines above" 메시지. |

### C) Batch / EventBridge / CloudWatch 스크립트

| 파일 | 변경 요약 |
|------|-----------|
| `scripts/infra/batch_video_setup.ps1` | Job queue computeEnvironmentOrder 업데이트 시 객체 → JSON → 임시 파일 → `aws batch update-job-queue --cli-input-json file://...`. 이스케이프/따옴표 문제 회피. |
| `scripts/infra/eventbridge_deploy_video_scheduler.ps1` | (이미 파일 기반 put-targets 사용. 변경 없음.) |
| `scripts/infra/cloudwatch_deploy_video_alarms.ps1` | Job queue ARN을 `(aws ... 2>&1) \| Out-String`으로 받아 trim. cp949/UTF-8 환경 안정화. |

### D) 문서/가드

| 파일 | 변경 요약 |
|------|-----------|
| `docs/video_batch_production_runbook.md` | 상단에 "Source of truth (no silent fallback)" 섹션 추가. SSM JSON·entrypoint 경유 명시. Section 0 정리(SSM 수동 수정 금지). "One-shot execution (copy-paste PowerShell)" 블록 추가. Ops job 설명에 entrypoint 경유 명시. |
| `README.md` | "Video Batch (AWS Batch)" 단락 추가: source of truth(.env→SSM), 원테이크 순서는 runbook 참고. |

---

## 최종 원테이크 실행 (복붙 PowerShell)

저장소 루트에서 **PowerShell**로 실행. `<acct>`를 AWS 계정 ID로 교체 (예: `809466760795`). `.env`는 `.env.example`에서 복사 후 필수 값 채워 둔 상태여야 함.

```powershell
# UTF-8 (Windows cp949 / SSM JSON 방지)
$OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()

# a) .env 준비됨 가정 (copy .env.example .env 후 필수 키 입력)

# b) SSM bootstrap (.env -> /academy/workers/env JSON)
.\scripts\infra\ssm_bootstrap_video_worker.ps1 -Region ap-northeast-2 -EnvFile .env -Overwrite

# c) Batch in API VPC
$ecrUri = "<acct>.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:latest"
.\scripts\infra\recreate_batch_in_api_vpc.ps1 -Region ap-northeast-2 -EcrRepoUri $ecrUri

# d) EventBridge (최종 큐 이름 사용)
$q = (Get-Content docs\deploy\actual_state\batch_final_state.json -Raw | ConvertFrom-Json).FinalJobQueueName
.\scripts\infra\eventbridge_deploy_video_scheduler.ps1 -Region ap-northeast-2 -JobQueueName $q

# e) CloudWatch alarms
.\scripts\infra\cloudwatch_deploy_video_alarms.ps1 -Region ap-northeast-2 -JobQueueName $q

# f) Netprobe + production done check
.\scripts\infra\run_netprobe_job.ps1 -Region ap-northeast-2 -JobQueueName $q
.\scripts\infra\production_done_check.ps1 -Region ap-northeast-2
```

- 각 단계는 실패 시 **exit code 비0**로 종료. 모두 0이어야 함.
- 마지막에 `PRODUCTION DONE CHECK: PASS`가 나와야 실제 성공 증명 완료.
