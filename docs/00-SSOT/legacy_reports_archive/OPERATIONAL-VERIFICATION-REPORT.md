# Full Rebuild SSOT v3 — 운영 정렬 점검 리포트

**목적:** 현재 AWS 실제 상태가 SSOT v3와 정렬되어 있는지 운영 관점에서 검증.  
**코드 수정 없음. 점검 리포트만 출력.**

**SSOT 기준 (env/prod.ps1 + INFRA-SSOT-V3.params.yaml):**
- **Region:** ap-northeast-2  
- **Batch CE:** academy-video-batch-ce-final, academy-video-ops-ce  
- **Batch Queue:** academy-video-batch-queue, academy-video-ops-queue  
- **JobDef:** academy-video-batch-jobdef, academy-video-ops-reconcile, academy-video-ops-scanstuck, academy-video-ops-netprobe  
- **EventBridge rules:** academy-reconcile-video-jobs, academy-video-scan-stuck-rate  
- **EventBridge role:** academy-eventbridge-batch-video-role  
- **ASG:** academy-messaging-worker-asg, academy-ai-worker-asg  
- **API EIP:** eipalloc-071ef2b5b5bec9428  

---

## [1] Batch 영역 점검

### 실행 명령

```bash
aws batch describe-compute-environments --region ap-northeast-2 --output json
aws batch describe-job-queues --region ap-northeast-2 --output json
```

### SSOT 기준

- **CE 허용 목록:** academy-video-batch-ce-final, academy-video-ops-ce (정확히 2개).
- **Queue 허용 목록:** academy-video-batch-queue, academy-video-ops-queue (정확히 2개).

### 점검 항목

| 항목 | 기준 | 판정 |
|------|------|------|
| CE 외 SSOT 미정의 CE 존재 | 없어야 함 | □ PASS / □ FAIL |
| CE별 status/state | (아래 표에 조회 결과 기입) | |
| Queue 외 SSOT 미정의 Queue 존재 | 없어야 함 | □ PASS / □ FAIL |

### CE 조회 결과 (기입)

| computeEnvironmentName | status | state |
|------------------------|--------|-------|
| (describe-compute-environments 결과에서 .computeEnvironments[].name, .status, .state 기입) | | |

### Queue 조회 결과 (기입)

| jobQueueName | state |
|--------------|-------|
| (describe-job-queues 결과에서 .jobQueues[].jobQueueName, .state 기입) | |

### [1] 판정

- SSOT에 정의되지 않은 CE가 1개라도 있으면 **FAIL**.
- SSOT에 정의되지 않은 Queue가 1개라도 있으면 **FAIL**.
- **결과:** □ PASS / □ FAIL

---

## [2] Job Definition 점검

### 실행 명령

```bash
aws batch describe-job-definitions --status ACTIVE --region ap-northeast-2 --output json
```

### SSOT 기준

- **JobDef 이름 허용 목록:** academy-video-batch-jobdef, academy-video-ops-reconcile, academy-video-ops-scanstuck, academy-video-ops-netprobe (4종).

### 점검 항목

| 항목 | 기준 | 판정 |
|------|------|------|
| ACTIVE JobDef가 위 4종 이름만 존재 | 다른 이름의 ACTIVE JobDef 없어야 함 | □ PASS / □ FAIL |
| JobDef별 ACTIVE revision 개수 | (아래 표에 기입) | |
| revision 5개 이상 누적 | 있으면 WARN | □ WARN / □ 해당 없음 |

### JobDef별 ACTIVE revision 개수 (기입)

| jobDefinitionName | ACTIVE revision 개수 |
|-------------------|----------------------|
| academy-video-batch-jobdef | |
| academy-video-ops-reconcile | |
| academy-video-ops-scanstuck | |
| academy-video-ops-netprobe | |
| (그 외 이름 있으면 기입 → SSOT 외 존재 시 FAIL) | |

### [2] 판정

- SSOT 4종 외 ACTIVE JobDef가 있으면 **FAIL**.
- 동일 이름 기준 revision 5개 이상이면 **WARN**.
- **결과:** □ PASS / □ FAIL, □ WARN

---

## [3] EventBridge 점검

### 실행 명령

```bash
aws events list-rules --region ap-northeast-2 --output json
```

### SSOT 기준

- **Rule 허용 패턴:** academy-* (academy-reconcile-video-jobs, academy-video-scan-stuck-rate).
- **상태:** 둘 다 ENABLED여야 함.

### 점검 항목

| 항목 | 기준 | 판정 |
|------|------|------|
| academy-* 외 rule 존재 | 없으면 정상, 있으면 WARN | □ WARN / □ 해당 없음 |
| academy-reconcile-video-jobs State | ENABLED | □ PASS / □ FAIL |
| academy-video-scan-stuck-rate State | ENABLED | □ PASS / □ FAIL |

### [3] 판정

- reconcile/scan rule이 ENABLED가 아니면 **FAIL**.
- academy-* 외 rule 있으면 **WARN**.
- **결과:** □ PASS / □ FAIL, □ WARN

---

## [4] IAM 점검

### 실행 명령

```bash
aws iam list-roles --output json
```

### SSOT 기준 (Batch/EventBridge 관련)

- **역할 이름:** academy-batch-service-role, academy-batch-ecs-instance-role, academy-batch-ecs-instance-profile(인스턴스 프로파일), academy-video-batch-job-role, academy-batch-ecs-task-execution-role, academy-eventbridge-batch-video-role.

### 점검 항목

| 항목 | 기준 | 판정 |
|------|------|------|
| academy-batch*, academy-eventbridge* 중복/이상 패턴 | 동일 목적 중복 role 없어야 함 | □ PASS / □ WARN |
| orphan role 후보 | 최근 사용 없고 SSOT에 없는 academy-* role | (이름 나열) |

### [4] 판정

- **결과:** □ PASS / □ WARN, Orphan 후보: ___________

---

## [5] ECS/Cluster 점검

### 실행 명령

```bash
aws ecs list-clusters --region ap-northeast-2 --output json
```

### SSOT 기준

- Batch CE가 사용하는 ECS cluster는 AWS Batch가 자동 생성한 이름 (예: Batch_* 형태). SSOT에 별도 cluster 이름 없음.

### 점검 항목

| 항목 | 기준 | 판정 |
|------|------|------|
| Batch CE와 매칭되지 않는 cluster | Batch 외 용도 cluster 있으면 WARN | □ WARN / □ 해당 없음 |

### [5] 판정

- **결과:** □ PASS / □ WARN

---

## [6] ASG 점검

### 실행 명령

```bash
aws autoscaling describe-auto-scaling-groups --region ap-northeast-2 --output json
```

### SSOT 기준

- **ASG 허용 목록:** academy-messaging-worker-asg, academy-ai-worker-asg.

### 점검 항목

| 항목 | 기준 | 판정 |
|------|------|------|
| SSOT 외 ASG 존재 | academy-* 등 다른 ASG 있으면 WARN | □ WARN / □ 해당 없음 |

### [6] 판정

- **결과:** □ PASS / □ WARN

---

## [7] EIP 점검

### 실행 명령

```bash
aws ec2 describe-addresses --region ap-northeast-2 --output json
```

### SSOT 기준

- **API EIP:** eipalloc-071ef2b5b5bec9428 (API 전용).

### 점검 항목

| 항목 | 기준 | 판정 |
|------|------|------|
| API 외 사용중 EIP | 다른 allocationId로 할당된 EIP 있으면 WARN | □ WARN / □ 해당 없음 |

### [7] 판정

- **결과:** □ PASS / □ WARN

---

## [8] 최종 판정

### 1) 구조 정렬

- [1] Batch CE/Queue: □ PASS / □ FAIL  
- [2] JobDef: □ PASS / □ FAIL  
- [3] EventBridge: □ PASS / □ FAIL  
- [4]~[7]: WARN만 있을 수 있음.

**구조 정렬:** □ PASS (모든 FAIL 없음) / □ FAIL (하나라도 FAIL)

### 2) 잔재 존재 여부

- SSOT에 없는 리소스(CE, Queue, JobDef, Rule, ASG, EIP 등): □ 없음 / □ 있음 (적요: ___________)

### 3) 실전 투입 안전도 등급

| 등급 | 조건 |
|------|------|
| **A** | 구조 정렬 PASS, 잔재 없음, WARN 없음 또는 최소 |
| **B** | 구조 정렬 PASS, 잔재 없음, WARN 있음 (정리 권장) |
| **C** | 구조 정렬 FAIL 또는 잔재 있음 (조치 후 재점검) |

**실전 투입 안전도 등급:** □ A / □ B / □ C

---

## 참고: 현재 환경에서의 실행 제한

본 문서 작성 시점에 **AWS 자격증명(UnrecognizedClientException)** 으로 인해 실제 describe/list 호출은 수행하지 않았습니다.  
위 표의 □ 및 (기입) 항목은 **로컬에서 아래 명령을 실행한 뒤**, 출력 결과를 위 표에 옮겨 채우고 판정하면 됩니다.

```powershell
$r = "ap-northeast-2"
aws batch describe-compute-environments --region $r --output json
aws batch describe-job-queues --region $r --output json
aws batch describe-job-definitions --status ACTIVE --region $r --output json
aws events list-rules --region $r --output json
aws iam list-roles --output json
aws ecs list-clusters --region $r --output json
aws autoscaling describe-auto-scaling-groups --region $r --output json
aws ec2 describe-addresses --region $r --output json
```

**코드 수정 없음. 리포트만 출력.**
