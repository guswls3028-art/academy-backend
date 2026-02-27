# Academy SSOT v4 — 상태·멱등·Evidence 계약

**역할:** scripts/v4가 따를 멱등 규칙, Wait 타임아웃, Netprobe 게이트, Evidence, Legacy kill-switch, PruneLegacy 계약.

---

## 1. 멱등 규칙

| 리소스 | Ensure 규칙 |
|--------|-------------|
| Batch CE | Describe 없으면 Create. INVALID → Queue DISABLED → CE DISABLED → delete → **Wait 삭제** → Create → **Wait VALID/ENABLED**. |
| Batch Queue | Describe 없으면 Create(CE ARN). state=ENABLED, computeEnvironmentOrder SSOT 일치. |
| JobDef | Describe ACTIVE 없으면 Register. drift 시에만 새 revision. |
| ASG | LT 변경 시 새 버전 → ASG 새 LT만 업데이트. **DesiredCapacity 현재값 유지.** 0 덮어쓰기 금지. |
| EventBridge | Rule 없으면 put-rule 후 put-targets. Rule 있으면 Target만 put-targets. |
| SSM | get-parameter 후 put-parameter --overwrite. |
| RDS/Redis SG | describe-security-groups → 없으면 authorize-security-group-ingress. |
| ECR | create-repository 없으면 생성. |

---

## 2. Wait 루프 (타임아웃)

| 대기 구간 | 조건 | 타임아웃 |
|-----------|------|----------|
| CE 삭제 후 | describe-compute-environments에서 해당 CE 없음 | 300초 |
| CE 생성 후 | status=VALID, state=ENABLED | 600초 |
| Queue 삭제 후 | describe-job-queues에서 해당 Queue 없음 | 180초 |
| ASG 삭제 후 | describe-auto-scaling-groups에서 해당 ASG 없음 | 300초 |
| ECS cluster 삭제 후 | status=INACTIVE 또는 목록에서 없음 | 120초 |
| IAM role 삭제 후 | get-role NoSuchEntity | 60초 |
| EventBridge rule 삭제 후 | describe-rule ResourceNotFoundException | 120초 |
| Netprobe Job | status=SUCCEEDED | 1200초. RUNNABLE 정체 300초 초과 시 throw. |

폴링 간격: 10~15초. **고정 Sleep 금지.**

---

## 3. Netprobe 게이트

- Ops Queue에 **academy-video-ops-netprobe** Job 제출.
- **성공:** status=SUCCEEDED.
- **실패 시:** FAILED/TIMEOUT/RUNNABLE 정체 → **throw**, 배포 중단.

---

## 4. Evidence 표 필수 컬럼

- batchVideoCeArn, batchVideoCeStatus, batchVideoCeState
- videoQueueArn, videoQueueState
- videoJobDefRevision, videoJobDefVcpus, videoJobDefMemory
- opsCeArn, opsCeStatus, opsCeState
- opsQueueArn, opsQueueState
- eventBridgeReconcileState, eventBridgeScanStuckState
- netprobeJobId, netprobeStatus
- asgMessagingDesired/Min/Max/LtVersion, asgAiDesired/Min/Max/LtVersion
- apiInstanceId, apiBaseUrl, apiHealth
- ssmWorkersEnvExists, ssmShapeCheck

---

## 5. Legacy kill-switch

- **단일 진입점:** 모든 배포는 `scripts/v4/deploy.ps1`만 실행.
- **CI 가드:** workflow에서 `scripts/infra/*.ps1` 실행/호출/Start-Process/pwsh -File scripts/infra 발견 시 **즉시 실패**.

---

## 6. PruneLegacy 삭제 계약

- **의존성 순서:** EventBridge targets 제거 → rules 삭제 → Batch queues DISABLED→삭제 → CE DISABLED→삭제 → JobDef deregister(SSOT 외만) → ASG(min=0 desired=0→force-delete) → ECS cluster → IAM(detach/delete inline→delete role) → ECR(SSOT 외) → SSM(SSOT 외) → EIP(미연결만 release).
- **예외:** RDS/Redis 기본 삭제 금지. API EIP allocationId 보호. params.yaml에 정의된 모든 이름 보호.
- 각 삭제 후 **describe 폴링 Wait** 필수.
