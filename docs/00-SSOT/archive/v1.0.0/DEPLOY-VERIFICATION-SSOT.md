# Deploy Verification SSOT — V1.0.0

**Version:** V1.0.0 (Locked)
**Effective:** 2026-03-11
**Authority:** 본 문서가 배포 검증의 **유일한 진실 소스(SSOT)**이다. 기존 문서(V1-DEPLOYMENT-VERIFICATION.md, DEPLOYMENT-STABILIZATION-PLAN.md, V1-OPERATIONS-GUIDE.md §4, DEPLOYMENT-TRUTH-REPORT.md)의 검증 관련 내용과 충돌 시 **본 문서가 우선**한다.

---

## 0. 버전 정책

- 본 문서는 **V1.0.0**으로 고정한다. 변경 시 버전 번호를 올린다.
- 모든 배포 검증은 본 문서의 정의만 따른다.
- 다른 문서의 검증 관련 내용은 본 문서에 대한 **참조(reference)**로만 유효하다.

---

## 1. 배포 경로 정의 (Canonical)

| ID | 경로 | 트리거 | 이미지 빌드 | 배포 방식 |
|----|------|--------|-------------|-----------|
| **P1** | CI 자동 배포 | `git push origin main` | GitHub Actions `v1-build-and-push-latest.yml` (OIDC, arm64, :latest) | 빌드 완료 후 `deploy-api-refresh` job → API ASG instance refresh |
| **P2** | 수동 정식 배포 | 운영자 실행 | GitHub Actions (빌드 별도, deploy.ps1은 SkipBuild=true) | `pwsh scripts/v1/deploy.ps1 -AwsProfile default` |
| **P3** | Video Batch 단독 | `docker/video-worker/**` 변경 또는 workflow_dispatch | `video_batch_deploy.yml` | Batch JobDef 업데이트 + CDN purge |

**금지 경로:** Rapid deploy (cron-based, in-place container replace) — 모든 rapid deploy 스크립트에 guard exit 적용 완료.

---

## 2. 헬스체크 엔드포인트 정의

| 엔드포인트 | 용도 | DB 의존 | 정상 응답 | 사용처 |
|-----------|------|---------|----------|--------|
| `/healthz` | **Liveness** (ALB target health) | No | 200 항상 | ALB health check, 배포 모니터링 |
| `/health` | **Readiness** (전체 의존성) | Yes (SELECT 1) | 200 또는 503 | smoke test, 관측, 배포 후 검증 |
| `/readyz` | **Readiness** (명시적) | Yes | 200 또는 503 | K8s 스타일 readiness (예비) |

**ALB 헬스체크 SSOT 설정** (params.yaml `api.healthPath`):
- Path: `/healthz`
- Interval: 30s
- Timeout: 5s (권장 10s)
- Healthy threshold: 2
- Unhealthy threshold: 3

---

## 3. 배포 후 검증 절차 (MANDATORY)

배포 후 아래 5단계를 **반드시 순서대로** 실행한다. 하나라도 FAIL이면 배포 완료로 인정하지 않는다.

### Stage 1: CI/CD 빌드 확인

```bash
# GitHub Actions 최근 run 상태
gh run list --limit 3

# 특정 run 상세
gh run view <RUN_ID>
```

**PASS 조건:** 최신 `v1-build-and-push-latest` run이 `completed/success`

### Stage 2: API 헬스체크

```bash
# Liveness (ALB)
curl -s -o /dev/null -w "%{http_code}" https://api.hakwonplus.com/healthz
# → 200

# Readiness (Full)
curl -s -w "\n%{http_code}" https://api.hakwonplus.com/health
# → 200 + JSON body
```

**PASS 조건:** `/healthz` → 200 AND `/health` → 200

### Stage 3: ASG 인스턴스 상태

```bash
# API ASG instance refresh 상태
source .env && aws autoscaling describe-instance-refreshes \
  --auto-scaling-group-name academy-v1-api-asg \
  --query 'InstanceRefreshes[0].{Status:Status,Pct:PercentageComplete}'

# API 인스턴스 health
source .env && aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names academy-v1-api-asg \
  --query 'AutoScalingGroups[0].Instances[*].[InstanceId,HealthStatus,LifecycleState]'
```

**PASS 조건:**
- Instance refresh: `Successful` (100%) 또는 진행 중 아님
- 모든 InService 인스턴스: `Healthy`
- InService 인스턴스 수 ≥ `api.asgMinSize` (현재 1)

### Stage 4: 워커 및 큐 상태

```bash
# Messaging/AI ASG (idle 시 0대 정상)
source .env && aws autoscaling describe-auto-scaling-groups \
  --auto-scaling-group-names academy-v1-messaging-asg academy-v1-ai-asg \
  --query 'AutoScalingGroups[*].{Name:AutoScalingGroupName,Desired:DesiredCapacity,InService:length(Instances[?LifecycleState==`InService`])}'

# SQS 큐 적체 확인
source .env && for Q in academy-v1-messaging-queue academy-v1-ai-queue; do
  URL=$(aws sqs get-queue-url --queue-name $Q --query QueueUrl --output text)
  echo "=== $Q ==="
  aws sqs get-queue-attributes --queue-url "$URL" --attribute-names All \
    --query 'Attributes.{Visible:ApproximateNumberOfMessages,InFlight:ApproximateNumberOfMessagesNotVisible}'
done

# DLQ 확인
source .env && for Q in academy-v1-messaging-queue-dlq academy-v1-ai-queue-dlq; do
  URL=$(aws sqs get-queue-url --queue-name $Q --query QueueUrl --output text 2>/dev/null)
  if [ -n "$URL" ]; then
    echo "=== $Q ==="
    aws sqs get-queue-attributes --queue-url "$URL" --attribute-names All \
      --query 'Attributes.ApproximateNumberOfMessages'
  fi
done
```

**PASS 조건:**
- 워커 ASG: InService == Desired (idle 시 0/0 허용)
- SQS Visible ≤ `observability.sqsQueueDepthThreshold` (100)
- DLQ Visible ≤ `observability.sqsDlqDepthThreshold` (5), 0이 이상적

### Stage 5: 인프라 정합성 (Drift)

```powershell
# Drift 확인 (읽기 전용)
pwsh scripts/v1/deploy.ps1 -Plan -AwsProfile default
```

또는 자동화 스크립트:

```powershell
pwsh scripts/v1/run-deploy-verification.ps1 -AwsProfile default
```

**PASS 조건:** Drift 항목 0개 또는 허용된 항목(문서화된 예외)만 존재

---

## 4. 모니터링 루프 검증 (반복 점검용)

CI/CD 빌드 완료 대기 또는 주기적 모니터링 시 아래 축약 절차를 사용한다.

| # | 항목 | 명령 | 정상 판단 |
|---|------|------|-----------|
| 1 | Git 최근 커밋 | `git fetch origin && git log origin/main --oneline -5` | HEAD 변경 여부 확인 |
| 2 | CI 빌드 상태 | `gh run list --limit 5` | 최신 run `completed/success` |
| 3 | API liveness | `curl -s -o /dev/null -w "%{http_code}" https://api.hakwonplus.com/healthz` | 200 |
| 4 | API ASG | `aws autoscaling describe-auto-scaling-groups --auto-scaling-group-names academy-v1-api-asg --query '...'` | Healthy, InService |
| 5 | Worker ASG | 위 Stage 4 명령 | Desired == InService |
| 6 | SQS 적체 | 위 Stage 4 명령 | Visible ≤ threshold |

**보고 형식:**

```
DEPLOY-MONITOR [timestamp]
━━━━━━━━━━━━━━━━━━━━━━━━━
Git:     [HEAD commit]
CI/CD:   [build status]
API:     [healthz result]
ASG:     [instance status]
Workers: [worker status]
SQS:     [queue depth]
Verdict: [PASS | ACTION_REQUIRED: <detail>]
━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 5. 배포 유형별 검증 범위

| 배포 유형 | 필수 검증 Stage | 비고 |
|-----------|----------------|------|
| P1 (CI 자동) | Stage 1, 2, 3 | 워커/SQS는 변경 없으면 생략 가능 |
| P2 (수동 정식) | Stage 1~5 전체 | Drift 포함 전체 검증 |
| P3 (Video Batch) | Stage 1 + Batch 전용 | `verify-video-batch-connection.ps1` 추가 |
| SSM env 변경 | Stage 2 + API 재시작 확인 | `refresh-api-env.ps1` 후 /health 200 확인 |
| params.yaml 변경 | Stage 1~5 + Drift 필수 | SSOT 변경 = 전체 검증 |

---

## 6. 임계값 SSOT (params.yaml 기준)

본 섹션의 값은 `docs/00-SSOT/v1/params.yaml`에서 관리한다. 여기서는 검증 시 사용하는 판단 기준만 정리한다.

| 항목 | 임계값 | params.yaml 경로 | 의미 |
|------|--------|------------------|------|
| API ASG min | 1 | `api.asgMinSize` | InService ≥ 1 필수 |
| API ASG max | 2 | `api.asgMaxSize` | |
| Instance refresh warmup | 300s | `api.instanceRefreshInstanceWarmup` | 새 인스턴스 안정화 대기 |
| ALB health path | /healthz | `api.healthPath` | Liveness only |
| SQS queue depth alarm | 100 | `observability.sqsQueueDepthThreshold` | |
| SQS DLQ depth alarm | 5 | `observability.sqsDlqDepthThreshold` | |
| Batch queue depth alarm | 50 | `videoBatch.observability.queueDepthAlarmThreshold` | |
| Batch failed jobs alarm | 5 | `videoBatch.observability.failedJobsAlarmThreshold` | |
| Log retention | 30d | `observability.logRetentionDays` | |
| Messaging visibility | 900s | `messagingWorker.visibilityTimeoutSeconds` | |
| AI visibility | 1800s | `aiWorker.visibilityTimeoutSeconds` | |
| Video job timeout (std) | 21600s (6h) | `videoBatch.standard.jobTimeoutSeconds` | |
| Video job timeout (long) | 43200s (12h) | `videoBatch.long.jobTimeoutSeconds` | |
| Video stuck heartbeat (std) | 20min | `videoBatch.standard.stuckHeartbeatAgeMinutes` | |
| Video stuck heartbeat (long) | 45min | `videoBatch.long.stuckHeartbeatAgeMinutes` | |

---

## 7. 검증 스크립트 매핑

| 스크립트 | 역할 | Stage 대응 | 출력 |
|----------|------|-----------|------|
| `scripts/v1/run-deploy-verification.ps1` | 전체 읽기전용 검증 | Stage 1~5 전체 | `reports/deploy-verification-latest.md` |
| `scripts/v1/verify.ps1` | 5-step 신규 환경 검증 | Bootstrap + Deploy + Evidence | Result table |
| `scripts/v1/deploy.ps1 -Plan` | Drift 전용 | Stage 5 | stdout |
| `scripts/v1/verify-video-batch-connection.ps1` | Video Batch 연결 | P3 전용 | stdout |
| `scripts/v1/core/evidence.ps1` | Evidence 스냅샷 | Stage 3~4 데이터 | `reports/audit.latest.md` |
| `scripts/v1/core/diff.ps1` | SSOT vs 실제 비교 | Stage 5 | `reports/drift.latest.md` |

---

## 8. 검증 결과 판정

### PASS (배포 완료 인정)
- 모든 필수 Stage의 조건 충족
- Drift 0개 또는 문서화된 예외만 존재
- DLQ 메시지 0개

### WARNING (조건부 통과)
- API healthy but /health 응답 >2s
- Worker ASG desired=0 (idle auto-stop, 정상 동작)
- DLQ 1~5개 (모니터링 필요)

### FAIL (배포 미완료)
- API /healthz ≠ 200
- ASG InService < asgMinSize
- Instance refresh 실패 (Failed/Cancelled)
- DLQ > sqsDlqDepthThreshold
- 미문서화 Drift 발견

---

## 9. 롤백 판단 기준

FAIL 판정 시 아래 순서로 조치한다:

1. **원인 진단** (로그, describe, CloudWatch)
2. **즉시 복구 가능:** SSM env 오류 → `refresh-api-env.ps1` → Stage 2 재검증
3. **코드 문제:** `git revert` + push → CI 자동 빌드 → P1 경로 재검증
4. **인프라 문제:** `deploy.ps1 -AwsProfile default` → Stage 1~5 전체 재검증

**롤백 제약:** ECR `:latest` 태그만 사용하므로, 이전 이미지 복원은 git revert + CI 재빌드가 유일한 경로.

---

## 10. 검증 보고서 보관

| 파일 | 갱신 시점 | 보관 |
|------|-----------|------|
| `reports/deploy-verification-latest.md` | 매 P2 배포 후 | 최신 1건 (latest) |
| `reports/audit.latest.md` | 매 배포 후 evidence 수집 시 | 최신 1건 |
| `reports/drift.latest.md` | 매 배포 후 diff 실행 시 | 최신 1건 |
| `reports/ci-build.latest.md` | CI 빌드 성공 시 (자동) | 최신 1건 |
| `reports/V1-FINAL-REPORT.md` | 전체 검증 완료 시 | 최신 1건 |
| `reports/history/*-deploy-verification.md` | 매 검증 시 타임스탬프 사본 | 누적 |

---

## 11. 기존 문서 대체 관계

| 기존 문서 | 상태 | 대체 섹션 |
|-----------|------|-----------|
| `V1-DEPLOYMENT-VERIFICATION.md` | **Superseded by V1.0.0** | 전체 (본 문서 §3~§8) |
| `DEPLOYMENT-STABILIZATION-PLAN.md` §5 (Validation) | **Superseded** | 본 문서 §3 |
| `DEPLOYMENT-TRUTH-REPORT.md` §1~§5 | **Reference only** — 사실 기록 유효, 검증 절차는 본 문서 우선 | 본 문서 §1 |
| `V1-OPERATIONS-GUIDE.md` §4 (검증 시나리오) | **Superseded** | 본 문서 §3, §5 |
| `RUNBOOK-DEPLOY-AND-ENV.md` §5 (검증) | **Superseded** | 본 문서 §3 Stage 2 |
| `DEPLOY-TIMING-CHECKLIST.md` | **Reference only** — 타이밍 참고용, 검증 기준은 본 문서 | 본 문서 §6 |
| `CLAUDE.md` Post-Deploy Verification | **Superseded** | 본 문서 §3 |
| `MEMORY.md` Post-Deploy Verification | **Superseded** | 본 문서 §3 |

---

**V1.0.0 — Locked. 변경 시 버전 번호를 올릴 것.**
