# INFRA SSOT v3 — 상태·멱등·Evidence 계약

**역할:** 스크립트(scripts_v3·원테이크)가 따를 **멱등 규칙**, **Wait 루프**, **Netprobe 성공 기준**, **Evidence 출력** 계약.  
**기준 문서:** [INFRA-SSOT-V3.md](./INFRA-SSOT-V3.md). **기계용 값:** [INFRA-SSOT-V3.params.yaml](./INFRA-SSOT-V3.params.yaml).

---

## 1. 멱등 규칙 (Ensure 규칙)

모든 변경은 **Describe → Decision → Update/Create** 순서만 수행한다. 동일 절차를 여러 번 실행해도 최종 상태가 동일해야 한다.

| 리소스 | Ensure 규칙 | 비고 |
|--------|--------------|------|
| **Batch CE** | Describe 없으면 Create(템플릿). INVALID → Queue DISABLED → CE DISABLED → delete → Wait 삭제 → Create → Wait VALID → Enable CE/Queue. 수동 bootstrap 불필요. |
| **Batch Queue** | Describe 없으면 Create(CE ARN). state=ENABLED, computeEnvironmentOrder SSOT 일치. |
| **Job Definition** | vCPU/Memory/Image 변경 시에만 새 revision 등록. 동일 스펙이면 기존 ACTIVE 재사용. 이름만 사용, revision 하드코딩 금지. | 이미지 immutable tag 필수. |
| **ASG** | LT 변경 시 새 버전 생성 → ASG를 새 LT 버전으로만 업데이트. **DesiredCapacity는 현재값 유지.** 0으로 덮어쓰기 금지. | describe 후 동일하면 update 없음. |
| **EventBridge** | Rule 존재 시 **Target만** put-targets로 최신화. Rule 삭제 후 재생성 금지. Enable/Disable만 enable-rule/disable-rule. | ScheduleExpression 일치 확인. |
| **SSM** | get-parameter 후 put-parameter --overwrite. ssm_bootstrap_video_worker.ps1로만 갱신. | 값 shape는 SSM_JSON_SCHEMA 검증. |
| **RDS/Redis SG** | describe-security-groups → 기존 규칙 확인 후, 없으면 authorize-security-group-ingress(Batch SG → 5432/6379). | 중복 규칙 추가 방지. |
| **ECR** | create-repository 없으면 생성. 이미지 태그는 배포 파이프라인에서 immutable 보장. | :latest 금지(원테이크 시 FAIL). |

---

## 2. Wait 루프 (상태 전이 대기)

| 대기 구간 | 조건 | 타임아웃 | 실패 시 |
|-----------|------|----------|---------|
| **CE 삭제 후** | describe-compute-environments에서 해당 CE가 목록에 없음 | 300초 | FAIL, 수동 확인 |
| **CE 생성 후** | describe-compute-environments: status=VALID, state=ENABLED | 600초 | FAIL, 수동 확인 |
| **Netprobe Job** | describe-jobs: job status=SUCCEEDED (또는 FAILED로 종료) | 1200초(20분) | FAIL. RUNNABLE 정체 180초 초과 시에도 throw. |

폴링 간격: 10~15초 권장. 타임아웃 초과 시 **원테이크 전체를 FAIL**로 처리하고 Evidence에는 해당 리소스 상태를 기록한다.

---

## 3. Netprobe 계약

- **목적:** Ops Queue·Ops CE·Job Definition이 정상 동작하는지 검증.
- **방법:** Ops Queue에 **netprobe** Job Definition(academy-video-ops-netprobe)으로 Job 제출.
- **성공 기준:** Job status가 **SUCCEEDED**.
- **실행:** scripts_v3 내 Invoke-Netprobe (Ops Queue에 academy-video-ops-netprobe Job 제출).
- **실패 시:** SUCCEEDED가 아니면 **원테이크 FAIL (throw)**. FAILED 또는 타임아웃(20분) 또는 RUNNABLE 정체(180초) 시 배포 중단.

---

## 4. Evidence 출력 계약

배포 완료 후 반드시 아래 항목을 **표 형태**로 출력한다. 스크립트는 params.yaml의 이름을 사용해 describe-* 결과를 채운다.

| 출력 키 | 리소스 | 수집 방법 |
|---------|--------|-----------|
| batchVideoCeArn | Video CE | describe-compute-environments --compute-environments academy-video-batch-ce-final |
| batchVideoCeStatus | Video CE | 위 응답 status |
| batchVideoCeState | Video CE | 위 응답 state |
| videoQueueArn | Video Queue | describe-job-queues --job-queues academy-video-batch-queue |
| videoQueueState | Video Queue | 위 응답 state |
| videoJobDefRevision | Video JobDef | describe-job-definitions --job-definition-name academy-video-batch-jobdef --status ACTIVE, 최신 revision |
| videoJobDefVcpus / videoJobDefMemory | Video JobDef | 위 응답 containerProperties |
| opsCeArn / opsCeStatus / opsCeState | Ops CE | describe-compute-environments --compute-environments academy-video-ops-ce |
| opsQueueArn / opsQueueState | Ops Queue | describe-job-queues --job-queues academy-video-ops-queue |
| eventBridgeReconcileState | Reconcile rule | events describe-rule --name academy-reconcile-video-jobs → State |
| eventBridgeScanStuckState | Scan stuck rule | events describe-rule --name academy-video-scan-stuck-rate → State |
| netprobeJobId / netprobeStatus | Netprobe Job | run_netprobe_job.ps1 반환값 또는 describe-jobs |
| asgMessagingDesired / asgMessagingMin / asgMessagingMax / asgMessagingLtVersion | Messaging ASG | describe-auto-scaling-groups --auto-scaling-group-names academy-messaging-worker-asg |
| asgAiDesired / asgAiMin / asgAiMax / asgAiLtVersion | AI ASG | describe-auto-scaling-groups --auto-scaling-group-names academy-ai-worker-asg |
| apiInstanceId / apiBaseUrl / apiHealth | API | describe-addresses 또는 instance metadata + GET /health → 200 |
| ssmWorkersEnvExists / ssmShapeCheck | SSM | get-parameter /academy/workers/env, SSM_JSON_SCHEMA 필수 키 검증 → PASS/FAIL |

**산출물 위치:** `docs/02-OPERATIONS/actual_state/*.json` 및 콘솔 "VIDEO WORKER SSOT AUDIT" 블록.  
**최종 결과:** 위 항목 중 필수(CE status/state, Queue state, Netprobe SUCCEEDED, SSM shape)가 모두 정상이면 **PASS**, 하나라도 아니면 **FAIL**.

**이미지 digest:** JobDef가 `:latest` 등 태그만 사용하더라도, 배포 시 ECR `describe-images`로 해당 이미지의 **imageDigest**를 조회하여 Evidence 표에 반드시 포함한다. (추후 digest 기반 drift 판단 전환 시 활용.)

---

## 5. 동시 실행

- **현재:** 락(동시 실행 방지) 미구현. **동시에 원테이크 2회 이상 실행 금지**로 운영.
- **도입 시:** 락 전략(DynamoDB/S3/GitHub 환경 등)을 이 문서에 명시하고, 스크립트가 --lock 옵션으로 준수하도록 한다.

---

## 6. 스크립트 인터페이스 권장

| 옵션 | 의미 |
|------|------|
| --env | 환경(prod/staging). 기본 prod. |
| --dry-run | Describe·Decision만, 변경 없음. |
| --plan | 변경 목록(Plan 아티팩트)만 출력. |
| --apply | Update/Create 실행. |
| --lock | 동시 실행 방지. 미구현 시 문서에 "동시 실행 금지" 명시. |
| --verbose | 상세 로그. |

params.yaml 경로는 환경변수(예: `SSOT_PARAMS`) 또는 기본값 `docs/00-SSOT/INFRA-SSOT-V3.params.yaml`로 지정.

---

## 7. Legacy 실행 차단 (Kill-Switch)

- **단일 진입점:** 모든 배포는 `scripts_v3/deploy.ps1`만 실행한다.
- **CI 가드:** GitHub Actions 등 CI에서 `scripts/infra/*.ps1`(예: batch_video_setup.ps1, eventbridge_deploy_video_scheduler.ps1)을 **직접 호출하면 안 된다**. workflow에 denylist 검사 step을 두어, 레거시 스크립트 직접 실행 시 **즉시 실패**하도록 한다.
- **(선택)** 레거시 스크립트 상단에 deprecated guard: 직접 실행 시 `throw "DEPRECATED: Use scripts_v3/deploy.ps1"` 로 중단. 단, 다른 스크립트에서 dot-source로 불러오는 경우는 예외(환경변수 등으로 제어 가능).
