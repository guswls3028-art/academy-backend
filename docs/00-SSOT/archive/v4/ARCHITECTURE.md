# Academy 인프라 — Final Design v1.0 설계 문서 (A to Z)

**버전:** 1.0  
**단일 진입점:** `scripts/v4/deploy.ps1`  
**단일 SSOT:** `docs/00-SSOT/v4/params.yaml`

---

## 1. 네트워크 / 서브넷 / 라우팅 / SG

### 1.1 구성

- **Region:** ap-northeast-2
- **2-tier:** Public 서브넷 2개 + Private 서브넷 2개 (AZ 2개 분산)
- **NAT:** 1개 (Public 1AZ에 배치). Private 기본 라우트 0.0.0.0/0 → NAT
- **VPC Endpoint:** 사용하지 않음 (아웃바운드는 NAT 경유)
- **IGW:** VPC 1개에 1개, Public 라우트 테이블에 0.0.0.0/0 → IGW

### 1.2 서브넷

| 용도 | 개수 | 라우팅 | 용도 |
|------|------|--------|------|
| Public | 2 | 0.0.0.0/0 → IGW | ALB, NAT Gateway |
| Private | 2 | 0.0.0.0/0 → NAT | API ASG, Workers ASG, Batch CE, Build(선택) |

### 1.3 보안 그룹

| SG | 용도 | 비고 |
|----|------|------|
| sg-app | API ASG, Messaging/AI ASG, Build | 앱 인스턴스 공통. ALB → 80 허용 |
| sg-batch | Video/Ops Batch CE | ECS/Batch 전용, egress all |
| sg-data | RDS/Redis (5432, 6379 from sg-app/sg-batch) | 데이터 계층 |

**향후 개선:** ALB 전용 sg-alb 분리 가능(현재는 sg-app에 ALB→80 수신 포함). 이번 패스에서는 변경하지 않음.

### 1.4 라우팅 요약

- **Public RT:** 0.0.0.0/0 → igw-xxx, Public 서브넷 2개 연결
- **Private RT:** 0.0.0.0/0 → nat-xxx, Private 서브넷 2개 연결

---

## 2. 컴퓨팅 — API / ASG / Batch

### 2.1 API

- **ALB:** Public 서브넷, Internet-facing
- **Target Group:** academy-api-tg, /health 헬스체크
- **ASG:** min=1, max=2, desired=1. **desired는 덮어쓰지 않고 min/max 클램프만 적용**
- **인스턴스:** Private 서브넷, sg-app, t4g.medium (ARM64)
- **EIP:** 사용하지 않음. 접근은 ALB DNS로만

### 2.2 Messaging / AI 워커 ASG

- **서브넷:** Private
- **SG:** sg-app
- **용량:** min=1, max=10, desired=1 (정책 수렴 시 desired는 클램프만, 덮어쓰기 금지)
- **Scale-in protection:** ON
- **스케일 정책:** SQS backlog 기반 step scaling (+1 / -1)
  - Scale-out cooldown: 300s
  - Scale-in cooldown: 900s

### 2.3 Video Batch

- **CE:** EC2 타입, minvCpus=0, maxvCpus=10, instanceType=c6g.large
- **서브넷:** Private
- **SG:** sg-batch
- **1 job = 1 video,** CPU only
- **Queue / JobDef / EventBridge:** 기존 네이밍 유지, 수렴만 적용

### 2.4 Build

- **인스턴스:** 1대, 태그 기반. Private 또는 Public(선택). t4g.medium
- **역할:** 이미지 빌드·ECR 푸시

---

## 3. 데이터 — RDS / Redis / DynamoDB Lock

### 3.1 RDS / Redis

- **RDS:** academy-db — validate-only (삭제 금지)
- **Redis:** academy-redis — validate-only. 진행률은 `progress:{videoId}` 키 사용, DB는 최종 결과만

### 3.2 DynamoDB — video_job_lock

- **목적:** videoId 기준 중복 submit 원천 차단
- **테이블:** video_job_lock
- **PK:** videoId
- **TTL:** ttl 속성 지원
- **사용:** submit 전 conditional put — `attribute_not_exists(videoId)` 또는 status가 FAILED/SUCCEEDED이고 stale인 경우만 put. 실패 시 "이미 실행중" 로그

---

## 4. 배포 순서 및 게이트 조건

### 4.1 Ensure 순서 (고정)

1. **락 획득** (heartbeat + fencing token)
2. **Load params + 검증** (EcrRepoUri 필수 시 검사)
3. **Preflight** (identity, region, 권한)
4. **Drift 계산** → 표 출력
5. **(옵션)** PruneLegacy / PurgeAndRecreate
6. **Ensure 순서:**
   - Batch IAM
   - **Network** (VPC, Subnets, IGW, NAT, RT, SG) → 게이트: VPC·서브넷·NAT 존재
   - RDS/Redis validate + SG
   - SSM
   - ECR
   - **DynamoDB** (video_job_lock)
   - **Workers ASG** (Messaging, AI) — desired clamp, scaling policy, scale-in protection → 게이트: InService >= min
   - **Batch** (Video CE, Ops CE, Queue, JobDef, EventBridge) → 게이트: CE VALID/ENABLED, Queue ENABLED, JobDef ACTIVE
   - **API** (ALB, Target Group, Listener, ASG) → 게이트: ALB DNS로 /health 200
   - Build
7. **Netprobe** (선택)
8. **Evidence** 저장
9. **락 해제**

### 4.2 단계별 수렴 게이트

- **Network:** describe로 VPC·public/private 서브넷 2개씩·NAT 1개·SG 3개 존재 확인 후 다음 단계
- **Workers:** 각 ASG InService 인스턴스 수 >= min
- **Batch:** Video/Ops CE status=VALID, state=ENABLED; Queue state=ENABLED; JobDef ACTIVE
- **API:** ALB DNS에 대해 GET /health → 200

---

## 5. 멱등 규칙

- **:latest 태그 배포 금지.** Immutable tag 필수. EcrRepoUri 미지정이면 deploy 실패.
- **ASG desired:** 무조건 덮어쓰지 않음. min/max만 SSOT로 맞추고, desired는 현재값을 min~max로 **클램프**만 적용.
- **중복 방지:** DynamoDB video_job_lock으로 동일 videoId 재제출 차단.
- **락:** SSM deploy-lock을 heartbeat + fencing token으로 강화. 각 주요 단계 전 토큰 일치 확인.
- **재실행:** 동일 입력이면 모든 Ensure가 No-op으로 수렴.

---

## 6. 참조

- **기계 SSOT:** `params.yaml`
- **상태·게이트 계약:** `state-contract.md`
- **Discovery:** `reports/STEP-A-DISCOVERY-CURRENT-STATE.md`
- **운영:** `runbook.md`
