# Full Rebuild 안정성 보강 — 변경 요약

**목표:** 운영 난이도 최소화 + 자동 재건축 안정성 강화. 구조·JSON 템플릿·복잡도 유지.

---

## 1. 수정된 파일 (이번 변경만)

| 파일 | 항목 | 설명 |
|------|------|------|
| `scripts_v3/resources/eventbridge.ps1` | [1] | describe-rule try/catch, ruleExists 판정, 없으면 put-rule, put-targets 항상 |
| `scripts_v3/resources/batch.ps1` | [2][3] | INVALID 시 Queue 존재 시에만 DISABLED/ENABLED, 불필요한 Sleep 제거 |
| `scripts_v3/core/wait.ps1` | [4] | Wait-CEValidEnabled에서 statusReason에 "INVALID" 포함 시 즉시 throw |
| `scripts_v3/resources/jobdef.ps1` | [5] | Test-JobDefDrift에 retryStrategy.attempts, platformCapabilities, environment 추가 |
| `scripts_v3/deploy.ps1` | [6] | try/catch/finally로 Show-Evidence 항상 마지막 실행, 실패 시에도 throw 유지 |

---

## 2. 변경된 함수별 요약

### Ensure-EventBridgeRules (eventbridge.ps1)
- **describe-rule 안전화:** `describe-rule`을 try/catch로 감싸고, 성공 시에만 `ruleExists = ($null -ne $rule)`로 판정. catch 시 `ruleExists = $false`.
- **Rule 없을 때:** `ruleExists`가 false면 `put-rule` 실행 (reconcile / scan_stuck 각각 동일 적용).
- **put-targets:** 기존처럼 항상 실행. 로직 구조 유지.

### Ensure-VideoCE / Ensure-OpsCE (batch.ps1)
- **INVALID 분기 — Queue 존재 체크:** `update-job-queue ... DISABLED` 전에 `describe-job-queues`로 해당 Queue 존재 여부 확인. `jobQueues.Count -gt 0`일 때만 Queue DISABLED 호출 후, Queue state=DISABLED가 될 때까지 상태 기반 폴링(90s).
- **불필요한 sleep 제거:** 분기 진입 직후 `Start-Sleep -Seconds 5` 제거. CE DISABLED 대기만 상태 기반 폴링(120s) 유지.
- **Recreate 후 Queue Enable:** CE 재생성 후 `describe-job-queues`로 Queue 존재 확인. 존재할 때만 `update-job-queue ... ENABLED` 호출. Queue가 없었던 경우(처음부터 없음/삭제됨) skip.
- **delete 이후:** 기존처럼 `Wait-CEDeleted`만 사용. sleep 기반 대기 없음.

### Wait-CEValidEnabled (wait.ps1)
- **status=VALID AND state=ENABLED:** 기존 조건 유지.
- **statusReason 보강:** `$ce.statusReason`에 문자열 "INVALID"가 포함되면(`-like "*INVALID*"`) 즉시 throw. desiredvCpus 등 추가 조건 없음.

### Test-JobDefDrift (jobdef.ps1)
- **추가 비교 필드:**  
  - `retryStrategy.attempts` (job definition 루트)  
  - `platformCapabilities` (배열, Sort 후 JSON 문자열 비교)  
  - `containerProperties.environment` (배열, JSON 문자열 비교)
- **동작:** drift 없으면 revision 증가 없음(기존과 동일). drift 있을 때만 register.

### deploy.ps1 (진입점)
- **$netJobId / $netStatus:** 스크립트 상단에서 `""`로 초기화.
- **try 블록:** Preflight ~ Netprobe(또는 Skip)까지 전체 시퀀스.
- **catch:** `throw`로 재발생시켜 Ensure-* / Netprobe 실패 시 즉시 종료 유지.
- **finally:** `Show-Evidence -NetprobeJobId $netJobId -NetprobeStatus $netStatus` 항상 실행. 성공/실패 관계없이 Evidence 출력 후 스크립트 종료(또는 catch에서 throw로 종료).

---

## 3. Full Rebuild 흐름 다이어그램 (간단 텍스트)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  deploy.ps1 (AllowRebuild=true, -SkipNetprobe 선택)                      │
└─────────────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────┐
│ Invoke-PreflightCheck                                                    │
└────────┬────────┘
         ▼
┌─────────────────┐
│ Ensure-BatchIAM │  (roles + instance profile, 없으면 create)
└────────┬────────┘
         ▼
┌─────────────────┐     없음        ┌──────────────────┐
│ Ensure-VideoCE  │ ──describe empty──► create CE ──► Wait-CEValidEnabled
└────────┬────────┘     INVALID    ┌──────────────────┐
         │               ─────────►│ Queue 존재? DISABLED (폴링)             │
         │                          │ CE DISABLED (폴링)                       │
         │                          │ delete CE ──► Wait-CEDeleted           │
         │                          │ create CE ──► Wait-CEValidEnabled       │
         │                          │ Queue 존재? ENABLED                     │
         └──────────────────────────┘
         ▼
┌─────────────────┐  (동일 패턴: create path / INVALID recreate path)
│ Ensure-OpsCE    │
└────────┬────────┘
         ▼
┌─────────────────┐     없음
│ Ensure-VideoQueue│ ──describe empty──► create (CE ARN 연결)
│ Ensure-OpsQueue  │  DISABLED ──► CE 순서 맞춘 뒤 ENABLED
└────────┬────────┘
         ▼
┌─────────────────┐     no ACTIVE / drift
│ Ensure-*JobDef   │ ──► register-job-definition (4종: video, reconcile, scanstuck, netprobe)
└────────┬────────┘     drift 비교: image, vcpus, memory, command, roles, logConfig, timeout,
         │              retryStrategy.attempts, platformCapabilities, environment
         ▼
┌─────────────────────┐  rule 없음        put-rule (try/catch로 describe 안전화)
│ Ensure-EventBridgeRules │ ──────────────► put-targets 항상
└────────┬────────────┘
         ▼
┌─────────────────┐
│ Confirm-ASGState │  (조회만)
│ Confirm-SSMEnv   │
│ Confirm-APIHealth│
└────────┬────────┘
         ▼
┌─────────────────┐  -SkipNetprobe 아니면
│ Invoke-Netprobe │  submit job ──► SUCCEEDED 대기 (FAILED/TIMEOUT/RUNNABLE 정체 시 throw)
└────────┬────────┘
         ▼
┌─────────────────┐
│ finally:        │  (성공/실패 무관 항상 실행)
│ Show-Evidence   │  CE/Queue/JobDef(revision+digest)/EventBridge/ASG/API/SSM/Netprobe
└─────────────────┘
```

---

## 4. 하지 않은 것 ([7])

- JSON 템플릿 제거 없음
- digest 강제 비교 추가 없음
- 트랜잭션 롤백 구조 없음
- VPC Endpoint 강제 전환 없음
- 구조 리팩토링 없음
