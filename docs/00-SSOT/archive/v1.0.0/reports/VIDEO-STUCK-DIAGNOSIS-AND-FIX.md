# 영상 인코딩 멈춤 진단 및 해결

**상황:** 123123, 234234, 345345 영상이 "인코딩 중" 0%로 표시되나, 진행 상황 패널에는 "[7/7] 업로드", "95%" 표시

---

## 진단 결과 (2026-03-07)

### 1차 진단 (초기)
- 3개 Job RUNNING, reconcile 0 updated → 당시에는 동기화됨

### 2차 진단 (사용자 "꽤 오래 기다림" 보고)
- **CloudWatch 로그:** 트랜스코딩 100%(overall 85%) 완료 후 `[TRANSCODER] Progress pipe finished`에서 **멈춤**
- **원인:** processor 5~7단계(validate → thumbnail → **upload**) 중 한 단계에서 hang
- **조치:** 3개 Job 수동 terminate → `reconcile --resubmit`으로 재제출

### 해결 완료 (2026-03-07 08:58)
| Job | aws_job_id (재제출) | 상태 |
|-----|---------------------|------|
| video-9f4baf81 (video_id=198) | ada3a736-c8fe-4338-a9ef-196f41db0084 | RUNNABLE → RUNNING 예정 |
| video-12747d4a (video_id=199) | 96ade817-ac3a-4706-9975-5251ea0e24b8 | RUNNABLE → RUNNING 예정 |
| video-bc64d5e8 (video_id=200) | 3f63e724-b903-4d05-b419-200d0310c0b5 | RUNNABLE → RUNNING 예정 |

**재발 방지 검토:** validate/thumbnail/upload 단계에 타임아웃·로깅 강화 권장.

---

## 1. 배포와의 관계

**Video는 AWS Batch 사용** (Messaging/AI 워커와 다름)
- Batch Job은 EC2/Fargate에서 실행되며, **배포(deploy.ps1)와 직접 연관 없음**
- API ASG instance-refresh는 API 서버만 교체. Batch CE/Queue는 별도.
- **단, Batch 인스턴스가 Spot으로 종료**되거나, CE scale-in 시 **실행 중 Job이 중단**될 수 있음

**가능한 원인:**
1. Batch Job이 FAILED (Spot 중단, OOM, 네트워크 등)
2. Redis progress는 95%까지 기록됐으나, Job 완료 직전 실패 → DB/Video는 여전히 PROCESSING
3. Job이 RUNNING이지만 워커가 멈춤 (heartbeat 중단)

---

## 2. 진단 순서

### 2.1 Batch Job 상태 확인

```powershell
# RUNNING/SUCCEEDED/FAILED Job 목록
aws batch list-jobs --job-queue academy-v1-video-batch-queue --job-status RUNNING --region ap-northeast-2 --profile default --output table
aws batch list-jobs --job-queue academy-v1-video-batch-queue --job-status SUCCEEDED --region ap-northeast-2 --profile default --max-results 20 --output table
aws batch list-jobs --job-queue academy-v1-video-batch-queue --job-status FAILED --region ap-northeast-2 --profile default --max-results 20 --output table
```

### 2.2 Reconcile 실행 (DB ↔ Batch 동기화 + 실패 시 재제출)

```powershell
cd C:\academy
# v1 리소스 (SSOT: docs/00-SSOT/v1/params.yaml). .env.example 기준 academy-v1-* 사용.
pwsh -NoProfile -File scripts/v1/run-with-env.ps1 -- pwsh -NoProfile -Command "`$env:VIDEO_BATCH_JOB_DEFINITION='academy-v1-video-batch-jobdef'; `$env:VIDEO_BATCH_JOB_QUEUE='academy-v1-video-batch-queue'; python manage.py reconcile_batch_video_jobs --resubmit --older-than-minutes 0"
```

- `--resubmit`: Batch FAILED 또는 not_found(임계치 초과)인 Job을 RETRY_WAIT로 전환 후 **재제출**
- `--older-than-minutes 0`: 최근 Job도 대상 (기본 5분 이전만)
- `--dry-run`: 변경 없이 로그만 출력 (먼저 실행 권장)
- **로컬 .env:** `.env.example` 참고. `VIDEO_BATCH_JOB_DEFINITION=academy-v1-video-batch-jobdef` (구버전 academy-video-batch-jobdef 제거됨)

### 2.3 CloudWatch 로그 확인

```powershell
# 최근 Batch 워커 로그 (실패 원인 확인)
aws logs filter-log-events --log-group-name /aws/batch/academy-video-worker --start-time (Get-Date).AddHours(-2).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ") --region ap-northeast-2 --profile default --limit 50
```

---

## 3. R2 저장 여부

- **"[7/7] 업로드"** = processor 7단계(업로드) 진행 중 또는 완료
- **95%** = HLS 업로드가 거의 끝난 시점
- **완료되려면** 워커가 `job_complete` → DB READY, Redis READY, R2에 `tenants/{tenant_id}/media/hls/videos/{video_id}/...` 저장

**R2 확인 (wrangler):**
```powershell
wrangler r2 object list academy-video --prefix "tenants/" 2>$null
```

또는 Cloudflare 대시보드에서 academy-video 버킷 → `tenants/` prefix 확인

---

## 4. 해결책 요약

| 단계 | 명령 | 목적 |
|------|------|------|
| 1 | `aws batch list-jobs ... FAILED` | 실패한 Job 확인 |
| 2 | CloudWatch 로그 | 멈춤 원인 확인 (트랜스코딩 후 validate/thumbnail/upload hang 등) |
| 3 | `aws batch terminate-job --job-id <id> --reason "Stuck"` | 멈춘 RUNNING Job 수동 종료 |
| 4 | `reconcile_batch_video_jobs --resubmit --older-than-minutes 0` | FAILED Job 재제출 (v1 env 필수) |

**재제출 후:** Batch가 새 Job을 큐에 넣고, CE가 인스턴스를 띄우면(1~2분 cold start) 워커가 다시 실행됨. 원본 파일은 R2 raw에 있으므로 **다운로드부터 다시** 진행.

---

## 5. 배포 시 주의

- **Video Batch**는 deploy.ps1에서 CE/Queue/JobDef만 Ensure. **실행 중 Job에는 영향 없음**
- **Spot 인스턴스** 사용 시 중간에 종료될 수 있음 → Job FAILED → reconcile --resubmit으로 재시도
- EventBridge `academy-v1-reconcile-video-jobs`가 주기적으로 reconcile 실행 (기본 1시간). 수동으로 먼저 실행하면 빠르게 복구 가능
