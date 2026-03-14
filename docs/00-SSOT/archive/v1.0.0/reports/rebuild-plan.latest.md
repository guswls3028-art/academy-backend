# V1 Stateless Compute 재구축 계획 (SSOT 기준)

**목표:** 기존 꼬인 compute/orchestration 레이어를 “땜질”하지 않고, **데이터 레이어는 유지**한 채 **V1 SSOT 기준으로 stateless 인프라를 깨끗하게 재구축**한다.

## 유지 (절대 삭제/변경 최소)
- **RDS**: `academy-db`
- **Redis**: `academy-v1-redis`
- **SSM parameters**: `/academy/api/env`, `/academy/workers/env`, `/academy/rds/master_password` 등
- **ECR**: `academy-api`, `academy-ai-worker-cpu`, `academy-messaging-worker`, `academy-video-worker`
- **Cloudflare zone/domain**

## 재구축 대상 (compute 계층)
- API: **ALB/TG/LT/ASG**
- AI worker: **LT/ASG**
- Messaging worker: **LT/ASG**
- Batch: **CE/Queue/JobDef/Ops**
- **Security Groups**
- EventBridge: **rules/targets**

## 목표 구조 (운영 단순화)
- **API ASG**: min/desired/max = **1/1/2**
- **AI ASG**: min/desired/max = **1/1/5**
- **Messaging ASG**: min/desired/max = **1/1/3**
- **ALB**: 1
- **Batch**: `minvCpus=0`
- **NAT**: 0
- **EIP**: 0 *(사용자 관리 EIP 기준. AWS 서비스가 자체적으로 관리하는 주소는 예외 가능)*
- **SG**: ≤ 8

## 엔드포인트 정책
- **LB health endpoint:** `/healthz` (항상 200)
- **Readiness endpoint:** `/readyz` (DB/의존성 포함, 실패 시 503)

## 실행 원칙
- **SSOT 단일 진실:** 하드코딩 금지.
- **데이터 레이어 보호:** RDS/Redis/SSM/ECR/도메인 변경은 “명시적 작업”으로 분리.
- **레거시 제거:** 새 구조가 **healthy(healthz 200 + TG healthy ≥ 1)** 확인된 이후 단계적으로 삭제.

---

## PHASE A — 사전 스냅샷/인벤토리 (read-only)
- 산출물: `rebuild-inventory.latest.md`
- 수집:
  - ASG/LT/InstanceRefresh 상태
  - ALB/TG/Listener/TargetHealth
  - Batch CE/Queue/JobDef (VALID/ENABLED, minvCpus)
  - EventBridge rules/targets
  - SG 목록/참조(ENI)
  - EIP/NAT/RouteTables

## PHASE B — 새 compute 레이어 재구축 (파괴적)
1) (선행) **SSOT 정돈**
   - `network.natEnabled=false`
   - `messagingWorker.maxSize=3`
   - healthPath=`/healthz` 유지
2) **Compute 리소스 정리(삭제)**
   - 대상: 기존 API/worker ASG/LT/ALB/TG, Batch, EventBridge, SG
   - 보호: RDS/Redis/SSM/ECR/도메인 제외
3) **SSOT 기반 재배포**
   - `scripts/v1/deploy.ps1 -Env prod`

## PHASE C — 검증/보고서 갱신
- `scripts/v1/run-deploy-verification.ps1`
- PASS 조건:
  - `/healthz` 200
  - TG healthy ≥ 1
  - ASG 용량(1/1/2, 1/1/5, 1/1/3) SSOT 일치
  - NAT 0, (사용자 관리) EIP 0, SG ≤ 8
- 산출물 갱신:
  - `deploy-verification-latest.md`
  - `V1-FINAL-REPORT.md`

## PHASE D — 레거시 compute 단계 삭제
- 새 구조 안정화 후, 남아있는 legacy compute를 순서대로 삭제
- 매 단계마다 `rebuild-inventory.latest.md` 갱신 후 증거 기록

