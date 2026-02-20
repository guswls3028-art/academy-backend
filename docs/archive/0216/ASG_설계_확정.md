# 워커 ASG 설계 확정

**목적**: Video·AI 워커 오토스케일 설계를 한 문서에 확정.  
**참조**: 설계.md §3, 배포.md, 30K_기준.md §3.

**배경**: 1인 개발, 폭주형 트래픽, 자동화 우선. Lambda 수동 제어 대신 ASG Target Tracking으로 전환. 본 변경은 SSOT의 "단순성 유지" 원칙을 훼손하기 위한 것이 아니라, **구현 레이어 변경**이며 핵심 도메인·메시지 처리 정책은 변경하지 않음.

---

## 1. 결론 (워커 대기 수)

| 워커 | 평소 대기 | 일 들어오면 | 일 끝나면 |
|------|-----------|-------------|-----------|
| **Messaging** | **1** (항시 대기) | **복제 가능** (스케일 아웃) | 최소 1 유지 (Min=1) |
| **Video** | **0** | ASG가 인스턴스 생성 | scale-in으로 삭제 |
| **AI CPU** | **0** | ASG가 인스턴스 생성 | scale-in으로 삭제 |

- **Messaging**: Video/AI와 동일하게 **복제(스케일) 가능**. 다만 **항시 대기 1**이므로 Min=1, 필요 시 scale-out. (구현: Messaging 전용 ASG Min=1, 또는 500 단계에서 API EC2에 1대 고정.)

- **500 단계**: **ASG 사용.** 평소 0, 일 들어오면 ASG가 인스턴스 생성 → 처리 → scale-in으로 삭제 (당장_실행서와 동일).
- **설계**: 아래대로 Min=0, Max=20(또는 조정).

---

## 2. ASG 설계 (확정)

### 2.1 구조

```
SQS (ApproximateNumberOfMessagesVisible)
    → CloudWatch 메트릭 (커스텀 또는 SQS 메트릭)
    → ASG Target Tracking Scaling Policy
    → EC2 (Launch Template: t4g.medium Video / t4g.small or micro AI)
```

- **Min**: **0** (평시 비용 0. 첫 폭주 시 1~3분 부팅 지연 감수) 또는 **1** (안정성 우선 시).
- **Max**: **10** (500 단계 권장), **20** 이상은 필요 시 상향. 계정 vCPU 한도(Service Quota)와 동시 API/DB 처리 한도 고려해 보수적으로 설정.
- **scale-in**: ASG가 인스턴스 **terminate**. Worker **self-stop 제거**. 처리 중인 메시지는 완료 후 종료되도록 Lifecycle Hook 또는 워커 **SIGTERM** 처리 권장.
- **Warm Pool**: 일단 끔. 기본 ASG 동작은 "생성 → 처리 → terminate"이며, stopped 대기 풀은 Warm Pool을 켤 때만 생김.

### 2.2 메트릭

- **메트릭**: `ApproximateNumberOfMessagesVisible` (SQS 큐당).
- **타깃**: 인스턴스당 **15~25** 메시지. (예: 워커 1대가 분당 평균 20건 처리 가능 시 Target=20이 이론적 처리 균형점.)
- **Scale-out**: 큐 쌓이면 인스턴스 증가.
- **Scale-in**: 큐 비면 ASG 정책에 따라 인스턴스 감소(terminate).
- **SQS Visibility Timeout**: 워커 평균 처리 시간의 **최소 3배**로 유지 (작업 유실 방지).

### 2.3 폭주 시뮬레이션 (참고)

| Min | 시점 | 동작 |
|-----|------|------|
| **0** | 0초 | 인스턴스 없음 |
| **0** | ~60초 | CloudWatch 반영 → ASG 0→N 확장 시작 |
| **0** | 60~180초 | 첫 인스턴스 부팅 완료 |
| **1** | 0초 | 1대가 처리 시작 |
| **1** | ~60초 | ASG 판단 → N대로 확장 |
| 공통 | 이후 | N대 병렬 처리 → 큐 비면 scale-in으로 terminate |

→ 처음 1~3분은 상대적으로 느릴 수 있으나, 폭주형에서는 전체 완료 시간이 단축됨. 작업이 1~5분급이면 부팅 지연은 허용 가능.

### 2.4 구현 참고

- **스크립트**: `scripts/deploy_worker_asg.ps1` (SubnetIds, SecurityGroupId, IamInstanceProfileName 등).
- **환경 변수**: SSM Parameter Store `/academy/workers/env`에 .env 내용 저장 후 EC2 User Data에서 로드.
- **Max 계산**: Max ≥ ceil(예상 최대 큐 깊이 / Target) + 여유(1~2대). 500 단계 이론 25대급이어도 비용·한도로 10대 캡 권장.

### 2.5 단계별 적용

| 단계 | 방식 | 비고 |
|------|------|------|
| **500** | **ASG 사용** (Min=0, Max=20) | 평소 0, 일 들어오면 생성 → 처리 → scale-in 삭제. 당장_실행서. |
| **10K** | ASG 유지, Target Tracking 동일 | SQS 메트릭 → ASG. 30K_기준.md. |
| **30K** | ASG 유지, scale-in은 ASG만 | Lambda 제거, ASG 단일 제어. |

---

## 3. Scale-out / Scale-in 원칙 (30K 기준과 동일)

- **Scale-out**: SQS 깊이(또는 CloudWatch 메트릭) → ASG Target Tracking. 0→N 부팅 지연 감수.
- **Scale-in**: ASG가 terminate. 처리 중인 SQS 메시지는 **완료 후** 종료되도록 Lifecycle Hook 또는 워커 SIGTERM 처리 권장.
- **Lambda**: 수동 Lambda Start/Stop 제거. ASG 단일 제어.

---

## 4. 구현 필수 (Launch Template · 워커)

### 4.1 Launch Template User Data (반드시 포함)

ASG에서 가장 자주 나는 문제는 **인스턴스는 떴는데 컨테이너가 안 올라가는 경우**다. User Data에 아래를 넣을 것.

| 항목 | 내용 |
|------|------|
| **ECR 로그인** | `aws ecr get-login-password --region ap-northeast-2 \| docker login ...` |
| **docker run 재시도** | `docker run` 실패 시 재시도 로직 (예: 2~3회, sleep 후 재시도) |
| **결과 확인·로그** | `docker ps` 결과 및 스크립트 로그를 **CloudWatch Logs**로 전송해 두기 (인스턴스는 떴는데 컨테이너 미기동 시 원인 추적용) |

→ User Data 스크립트 마지막에 `docker ps` 출력을 CloudWatch에 남기면 디버깅에 유리함.

### 4.2 워커 SIGTERM 처리 (확인 필수)

ASG scale-in 시 인스턴스 terminate **전에 SIGTERM**이 전달됨. 이때 메시지 처리 중이면 **안전 종료**가 없으면 메시지 중복·유실 가능.

- **요구**: 워커 진입점에서 `signal.signal(signal.SIGTERM, graceful_shutdown)` 등으로 핸들러 등록.
- **동작**: SIGTERM 수신 시 현재 처리 중인 메시지 **완료 후** 종료. 새 메시지 수신은 중단.
- **코드 위치**: Video `sqs_main.py`, Messaging `sqs_main.py`, AI `ai_sqs_worker.py` 등에서 **이미 구현 여부 확인** 후, 없으면 추가.

---

## 5. ASG 타이밍 권장 (cooldown · grace period)

| 항목 | 권장값 | 비고 |
|------|--------|------|
| **Scale-in cooldown** | **120~300초** | 너무 짧으면 scale-in 진동. 폭주형이면 120초 이상. |
| **Health check grace period** | **300~600초** | EC2 부팅 + ECR pull + docker run 완료까지 고려. 5~10분 여유. |
| **Instance termination (SIGTERM → 종료)** | **30~60초** | 워커가 현재 작업 완료할 시간. Lifecycle Hook 사용 시 이 구간에서 완료 대기. |

→ 500 단계: scale-in cooldown **120초**, health check grace period **300초**로 시작 후, 부팅 시간 보고 600초로 늘려도 됨.

---

## 6. 문서 위치

- **당장 실행 (500)**: [당장_실행서.md](당장_실행서.md) — 500에서 ASG 사용. Messaging만 상시 1.
- **비용 예측**: [Worker_ASG_비용_예측.md](Worker_ASG_비용_예측.md) — 워커·R2 비용 (R2+Cloudflare 기준).
- **ASG 설계 확정**: 이 문서. §4 Launch Template User Data·§4.2 SIGTERM 확인·§5 cooldown·grace period 필수.
- **기존 참조**: [설계.md](../설계.md) §3, [배포.md](../배포.md), [30K_기준.md](../30K_기준.md) §3.
