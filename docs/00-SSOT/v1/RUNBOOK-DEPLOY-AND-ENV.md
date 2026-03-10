# V1 배포 및 런타임 env Runbook

**목적:** 재배포 후 비디오/메시징/AI 파이프라인이 끊기지 않도록, SSOT → 인프라 → 런타임 env가 항상 일치하도록 하는 절차.

---

## 0. 이미지 빌드 = GitHub Actions만 (강조)

**Docker 이미지 빌드·ECR 푸시는 반드시 GitHub Actions로만 수행한다.** 로컬 Docker 빌드·EC2에서의 빌드·수동 ECR 푸시는 사용하지 않는다.

- **워크플로:** `backend/.github/workflows/v1-build-and-push-latest.yml` — main 푸시 시 academy-base, academy-api, academy-video-worker, academy-messaging-worker, academy-ai-worker-cpu 이미지 빌드·ECR **:latest** 푸시 후 API ASG instance refresh 자동 실행.
- **정합:** SSOT `ecr.useLatestTag: true`로 풀배포(deploy.ps1)도 ECR **:latest** 사용. 이미지 빌드와 풀배포가 동일한 이미지 소스를 사용함. 상세: `docs/00-SSOT/v1/reports/INFRA-IMAGE-BUILD-DEPLOY-ALIGNMENT.md`.
- **코드 반영:** 이미지에 코드를 반영하려면 main에 푸시하면 된다. CI가 빌드·푸시·리프레시까지 수행한다.

---

## 1. AWS 프로필 (필수)

**V1 배포·검증·API 재배포 시 AWS 프로필은 반드시 `default`를 사용한다.** 프로필을 묻지 말고 항상 default로 실행한다.

- PowerShell: `-AwsProfile default` 인자 지정. 예: `pwsh -File scripts/v1/deploy.ps1 -AwsProfile default`
- verify-video-batch-connection.ps1: 스크립트 내부에서 `--profile default` 사용 (별도 지정 불필요)

---

## 2. 정식 V1 배포 (권장)

**한 번에 실행:** SSOT 기준 인프라 Ensure + SSM 동기화 + **기동 중 API에 env 반영**까지 수행.

```powershell
cd backend
pwsh -File scripts/v1/deploy.ps1 -AwsProfile default
# 시간 단축: -SkipNetprobe (Netprobe 생략)
```

**deploy.ps1가 하는 일 (요약):**

1. Bootstrap (SSM workers env, SQS, RDS password, ECR resolve)
2. Ensure 인프라 (Network, RDS, Redis, SQS, Batch CE/Queue/JobDef, ASG, ALB, API LT)
3. **Invoke-SyncEnvFromSSOT** — `/academy/api/env`, `/academy/workers/env`에 SSOT(SQS, Video Batch, Redis discovery) merge
4. **Invoke-RefreshApiEnvOnInstances** — 기동 중인 API 인스턴스에서 SSM 재조회 → `/opt/api.env` 갱신 → 컨테이너 재시작

→ 따라서 **deploy.ps1 한 번 실행**이면 API가 항상 SSOT와 동일한 VIDEO_BATCH_* / REDIS_HOST 등을 사용함.

---

## 3. SSM만 수동으로 바꾼 경우 (배포 없이)

SSM `/academy/api/env`만 수동 또는 `update-api-env-sqs.ps1`로 갱신한 경우, **기동 중 API는 예전 env를 쓰므로** 반드시 아래 중 하나 실행:

- **옵션 A:** API 인스턴스에만 반영  
  ```powershell
  pwsh -File scripts/v1/refresh-api-env.ps1 -AwsProfile default
  ```
- **옵션 B:** API ASG instance-refresh (인스턴스 교체 시 새 UserData로 SSM 재조회)

---

## 4. 재배포 후 끊김 방지 (동일 이슈 재발 방지)

| 원인 | 대응 |
|------|------|
| SSM은 갱신했지만 API 컨테이너가 부팅 시점 SSM만 사용 | deploy.ps1 사용( Sync 후 Refresh 자동 실행 ) 또는 수동 갱신 후 `refresh-api-env.ps1` 실행 |
| VIDEO_BATCH_* / REDIS_HOST 가 SSM에 없거나 구 이름 | deploy.ps1가 Sync에서 SSOT + Redis discovery로 채움. 수동이면 `update-api-env-sqs.ps1` 후 refresh |

---

## 5. 검증 (배포 후 권장)

```powershell
pwsh -File scripts/v1/verify-video-batch-connection.ps1
```
(스크립트 내부에서 `--profile default` 사용. AWS 프로필은 반드시 default.)

- SSM `VIDEO_BATCH_*` 가 v1 이름과 일치하는지, Batch 큐/JobDef/CE 존재 여부 확인.
- 상세: `docs/00-SSOT/v1/reports/API-VIDEO-BATCH-REDIS-CONNECTION-REFERENCE.md`

---

## 6. API만 재배포할 때 (풀배포 없이 API 서버만)

**전제:** 이미 한 번이라도 `deploy.ps1`를 실행해 SSM(`/academy/api/env`)이 SSOT와 맞춰져 있는 상태.

| 방법 | 스크립트 | 동작 | env 반영 |
|------|----------|------|-----------|
| **인스턴스 교체** | `api-refresh-only.ps1` 또는 `start-api-instance-refresh.ps1` | API ASG instance-refresh. 새 인스턴스가 뜨면 UserData가 **그 시점의 SSM**을 읽어 `/opt/api.env` 생성 후 컨테이너 실행. | ✅ 새 인스턴스 = 현재 SSM 사용. **문제없음.** |
| **인스턴스 유지** | `refresh-api-env.ps1` | 기동 중 API 인스턴스에 SSM send-command로 **현재 SSM** 재조회 → `/opt/api.env` 갱신 → 컨테이너 재시작. | ✅ 현재 SSM 적용. **문제없음.** |
| **재시작만** | `restart-api.ps1` | `docker restart academy-api` 만 수행. **SSM을 다시 읽지 않음.** | ❌ `/opt/api.env` 변경 없음. env 갱신이 목적이면 사용 금지. |

**정리:** 지금처럼 SSM이 이미 Sync된 상태라면, **API만 재배포**는 아래만 사용하면 됨.

- 새 이미지 반영·인스턴스 교체: `pwsh -File scripts/v1/api-refresh-only.ps1 -AwsProfile default` 또는 `pwsh -File scripts/v1/start-api-instance-refresh.ps1 -AwsProfile default`
- 이미지는 그대로, SSM만 기동 중 API에 다시 적용: `pwsh -File scripts/v1/refresh-api-env.ps1 -AwsProfile default`

**주의:** 위 스크립트 실행 시 **프로필은 반드시 default.** 사용자에게 프로필을 묻지 않는다.

---

## 7. 참고

- **SSOT:** `docs/00-SSOT/v1/params.yaml`
- **배포 스크립트:** `scripts/v1/` (deploy.ps1, sync_env.ps1, refresh-api-env.ps1)
- **연결 감사 보고서:** `docs/00-SSOT/v1/reports/V1-DEPLOYMENT-CONNECTION-AUDIT.md`
