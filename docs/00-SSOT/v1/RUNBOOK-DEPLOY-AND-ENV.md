# V1 배포 및 런타임 env Runbook

**목적:** 재배포 후 비디오/메시징/AI 파이프라인이 끊기지 않도록, SSOT → 인프라 → 런타임 env가 항상 일치하도록 하는 절차.

---

## 1. 정식 V1 배포 (권장)

**한 번에 실행:** SSOT 기준 인프라 Ensure + SSM 동기화 + **기동 중 API에 env 반영**까지 수행.

```powershell
cd backend
pwsh -File scripts/v1/deploy.ps1
# 필요 시: -AwsProfile default
# 시간 단축: -SkipNetprobe (Netprobe 생략)
```

**deploy.ps1가 하는 일 (요약):**

1. Bootstrap (SSM workers env, SQS, RDS password, ECR resolve)
2. Ensure 인프라 (Network, RDS, Redis, SQS, Batch CE/Queue/JobDef, ASG, ALB, API LT)
3. **Invoke-SyncEnvFromSSOT** — `/academy/api/env`, `/academy/workers/env`에 SSOT(SQS, Video Batch, Redis discovery) merge
4. **Invoke-RefreshApiEnvOnInstances** — 기동 중인 API 인스턴스에서 SSM 재조회 → `/opt/api.env` 갱신 → 컨테이너 재시작

→ 따라서 **deploy.ps1 한 번 실행**이면 API가 항상 SSOT와 동일한 VIDEO_BATCH_* / REDIS_HOST 등을 사용함.

---

## 2. SSM만 수동으로 바꾼 경우 (배포 없이)

SSM `/academy/api/env`만 수동 또는 `update-api-env-sqs.ps1`로 갱신한 경우, **기동 중 API는 예전 env를 쓰므로** 반드시 아래 중 하나 실행:

- **옵션 A:** API 인스턴스에만 반영  
  ```powershell
  pwsh -File scripts/v1/refresh-api-env.ps1
  ```
- **옵션 B:** API ASG instance-refresh (인스턴스 교체 시 새 UserData로 SSM 재조회)

---

## 3. 재배포 후 끊김 방지 (동일 이슈 재발 방지)

| 원인 | 대응 |
|------|------|
| SSM은 갱신했지만 API 컨테이너가 부팅 시점 SSM만 사용 | deploy.ps1 사용( Sync 후 Refresh 자동 실행 ) 또는 수동 갱신 후 `refresh-api-env.ps1` 실행 |
| VIDEO_BATCH_* / REDIS_HOST 가 SSM에 없거나 구 이름 | deploy.ps1가 Sync에서 SSOT + Redis discovery로 채움. 수동이면 `update-api-env-sqs.ps1` 후 refresh |

---

## 4. 검증 (배포 후 권장)

```powershell
pwsh -File scripts/v1/verify-video-batch-connection.ps1
```

- SSM `VIDEO_BATCH_*` 가 v1 이름과 일치하는지, Batch 큐/JobDef/CE 존재 여부 확인.
- 상세: `docs/00-SSOT/v1/reports/API-VIDEO-BATCH-REDIS-CONNECTION-REFERENCE.md`

---

## 5. 참고

- **SSOT:** `docs/00-SSOT/v1/params.yaml`
- **배포 스크립트:** `scripts/v1/` (deploy.ps1, sync_env.ps1, refresh-api-env.ps1)
- **연결 감사 보고서:** `docs/00-SSOT/v1/reports/V1-DEPLOYMENT-CONNECTION-AUDIT.md`
