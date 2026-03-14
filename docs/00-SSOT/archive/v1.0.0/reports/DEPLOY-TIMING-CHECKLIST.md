# 배포 타이밍·점검 체크리스트

배포 지연/타임아웃 원인 점검용 정리 (Netprobe, API 헬스, Evidence, 전체 타임아웃).

---

## 1. Netprobe 지연/실패

### 실제 동작 (Invoke-Netprobe)

- **위치**: `scripts/v1/resources/netprobe.ps1`
- **동작**: Ops Job Queue에 Batch Job 제출 → `describe-jobs`로 10초마다 폴링.
- **성공 조건**: Job 상태가 `SUCCEEDED`.
- **실패 조건**:
  - `RUNNABLE` 상태가 **RunnableFailSec(기본 300초)** 동안 유지 → throw.
  - `FAILED` → throw.
  - 전체 **TimeoutSec(기본 1200초)** 초과 → throw.

### Netprobe Job이 하는 일

- **JobDef**: `academy-v1-video-ops-netprobe`  
  - 이미지: ECR video-worker, 커맨드: `python manage.py netprobe`  
  - vCPU 1, 메모리 512MB, Job 타임아웃 120초.
- **의미**: Ops CE가 Job을 받아서 **한 번 실행 가능한지** 검사.  
  - RUNNABLE = Batch가 아직 **컴퓨트 리소스를 할당하지 못한 상태**.

### RUNNABLE에 오래 머무는 이유 (가능성)

| 원인 | 설명 |
|------|------|
| **Ops CE cold start** | Ops CE는 `minvCpus=0`. Job 제출 시 인스턴스 0대에서 스케일 아웃 → EC2 기동(수 분) → ECS 에이전트·이미지 풀 → RUNNING까지 4~7분 걸릴 수 있음. |
| **서브넷/보안그룹** | Ops CE가 사용하는 Private 서브넷, Batch용 SG가 없거나 잘못되면 인스턴스 기동/할당 실패. |
| **용량 부족** | `opsMaxvCpus: 2`(params), t4g.medium 1대 = 2 vCPU. Netprobe 1 vCPU만 필요하므로 이론상 가능. 인스턴스가 아직 안 떴으면 RUNNABLE 유지. |

### 점검 항목

- [ ] Ops CE 상태: `aws batch describe-compute-environments --compute-environments academy-v1-video-ops-ce --region ap-northeast-2` → status=VALID, state=ENABLED.
- [ ] Ops Queue: `aws batch describe-job-queues --job-queues academy-v1-video-ops-queue --region ap-northeast-2` → state=ENABLED.
- [ ] Ops CE ASG: 콘솔 또는 CLI로 `academy-v1-video-ops-ce-asg-*` 인스턴스 기동 여부 확인 (Job 제출 후 수 분 내).
- [ ] Netprobe Job 로그: CloudWatch `/aws/batch/academy-video-ops`, stream prefix `netprobe` → 실패 시 이유 확인.

### 권장 조정

- **RunnableFailSec**: cold start 고려해 **300 → 600**(10분) 권장.  
  - `deploy.ps1`에서 `Invoke-Netprobe -TimeoutSec 1200 -RunnableFailSec 600` 등으로 상향.

---

## 2. API 헬스 체크 지연

### 현재 동작

- **Ensure-API-Instance** (api.ps1): ASG 인스턴스 대기 → SSM 온라인 대기 → **Wait-ApiHealth200** 300초.
- **Wait-ApiHealth200** (wait.ps1): `GET {ApiBaseUrl}/health` 10초 간격, 300초 타임아웃.  
  - 타임아웃 시 **catch**로 경고만 하고 배포는 계속 진행(이미 적용됨).

### ALB Target Group 헬스 설정

- **위치**: `scripts/v1/resources/alb.ps1` — Target Group 생성 시.
- **설정**:
  - `health-check-path`: SSOT `api.healthPath` (기본 `/health`).
  - `health-check-interval-seconds`: **30**.
  - `healthy-threshold-count`: **2** → 2회 연속 성공 시 healthy.
  - `unhealthy-threshold-count`: **3**.
  - `--health-check-timeout-seconds`: 미지정 시 AWS 기본 **5초**.

### 인스턴스가 헬스 실패하는 이유 (가능성)

| 원인 | 설명 |
|------|------|
| **앱 기동 지연** | 인스턴스는 올라왔지만 앱이 8000 포트/health 응답 전에 ALB가 3회 실패로 unhealthy 처리. |
| **Draining** | 인스턴스 리프레시/교체 시 기존 인스턴스가 draining → 새 인스턴스가 아직 healthy가 아님. |
| **SG/라우팅** | ALB → 인스턴스 8000 포트 접근 불가(SG, NACL, 서브넷 라우팅). |

### 점검 항목

- [ ] Target Group 헬스:  
  `aws elbv2 describe-target-health --target-group-arn <ApiTargetGroupArn> --region ap-northeast-2`  
  → Target별 `Reason`, `State`.
- [ ] ALB 리스너/타깃그룹: `scripts/v1/resources/alb.ps1` 기준 포트 8000, 경로 `/health`.
- [ ] 앱 기동 시간: 컨테이너/프로세스가 health 응답을 몇 초 만에 주는지 확인.  
  - 필요 시 TG `health-check-interval-seconds` 30 유지, `health-check-timeout-seconds` **10**으로 늘려서 반영 가능.

### 참고

- 배포 스크립트에는 **Confirm-APIHealth** 단계가 없고, **Ensure-API** 안의 **Ensure-API-Instance**만 있음.  
  - API health 200 타임아웃 시 경고 후 진행하도록 이미 처리됨.

---

## 3. Evidence 단계 지연

### 동작

- **Show-Evidence** → **Get-EvidenceSnapshot** 호출.
- **위치**: `scripts/v1/core/evidence.ps1`.

### API/호출 개수 (순차 실행)

| # | 호출 |
|---|------|
| 1–2 | batch describe-compute-environments (Video CE, Ops CE) |
| 3–4 | batch describe-job-queues (Video, Ops) |
| 5 | batch describe-job-definitions (Video JobDef) |
| 6–7 | events describe-rule (reconcile, scan-stuck) |
| 8–9 | autoscaling describe-auto-scaling-groups (Messaging, AI) |
| 10 | (선택) ec2 describe-addresses (ApiAllocationId 있을 때) |
| 11 | autoscaling describe-auto-scaling-groups (API ASG) |
| 12 | Invoke-WebRequest API /health (5초 타임아웃) |
| 13 | ssm get-parameter (workers env) |
| 14 | ec2 describe-instances (Build 인스턴스) |

대략 **14~15회** 순차 호출. 네트워크/API 지연 시 **30초~2분** 걸릴 수 있음.

### 점검 항목

- [ ] Evidence 단계에 **시작/종료 시각 로그** 추가해 실제 소요 시간 측정 (추가됨).
- [ ] AWS API 지연: 동일 리전(ap-northeast-2)에서도 Describe 다수 시 수십 초 가능.

### 권장

- 배포를 **끊지 않으려면** Evidence 완료까지 여유 있게 **전체 배포 타임아웃**을 넉넉히 둠 (예: 25~30분).  
- Evidence 자체에 별도 타임아웃을 두려면, Get-EvidenceSnapshot을 일정 시간 초과 시 중단하거나 요약만 저장하도록 변경 가능.

---

## 4. 배포 단계별 예상 시간

| 단계 | 예상 시간 | 비고 |
|------|-----------|------|
| Bootstrap + Preflight + Drift | 1~2분 | |
| Ensure Network/RDS/Redis/ECR/Dynamo/ASG/Batch/EventBridge/ALB | 2~5분 | 변경 없으면 짧음 |
| Ensure API (ASG + SSM + API health 대기) | 최대 5분+ | API health 300초 타임아웃 시 약 5분 |
| Ensure Build | 1분 내외 | idempotent 시 짧음 |
| **Netprobe** | **5~10분** | cold start 시 5~7분, RunnableFailSec 300이면 실패 가능 |
| **Evidence** | **0.5~2분** | API 14+ 회 호출 |
| 락 해제 + COMPLETE | 수 초 | |

**총합**: 변경 없을 때 **약 10~15분**, Netprobe cold + API health 타임아웃까지 가면 **20분 넘을 수 있음**.

### 권장 타임아웃 (스크립트/실행 환경)

- **Netprobe RunnableFailSec**: 300 → **600** (10분).
- **Netprobe TimeoutSec**: 1200 유지 또는 **1800** (30분).
- **전체 배포 실행** (Cursor/CI 등): **최소 25~30분** 여유 두기.  
  - Evidence까지 포함해 20분으로 제한하면 타임아웃에 걸릴 수 있음.

---

## 5. 요약 권장 사항

1. **Netprobe**: `RunnableFailSec` 600으로 상향 (Ops CE cold start 허용).
2. **ALB**: Target Group health 확인; 앱 기동이 느리면 `health-check-timeout-seconds` 10 검토.
3. **Evidence**: 단계별 시작/종료 로그로 실제 소요 시간 측정 후, 전체 배포 타임아웃 25~30분 권장.
4. **단계별 로그**: 필요 시 `deploy.ps1`에 각 Ensure 전후 타임스탬프 출력해 어느 구간에서 지연되는지 확인.

이 문서는 `reports/drift.latest.md`, `reports/audit.latest.md`와 함께 배포 점검 시 참고용으로 사용할 수 있습니다.
