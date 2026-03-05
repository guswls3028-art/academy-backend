# Changelog — Final Design v1.0

## 요약

- **Step A:** Discovery 보고서 작성 완료.
- **Step B:** SSOT(params.yaml)·ARCHITECTURE.md·ssot.ps1·리소스 스크립트를 Final Design v1.0 기준으로 정리. 네트워크/API/Workers/Batch/ECR/락 관련 변수 추가. ECR immutable tag 필수 시 EcrRepoUri 미지정이면 deploy 실패.
- **Step C~E, G:** 구현 완료. 2-tier Network Ensure, RDS/Redis Ensure, Workers ASG clamp + SQS scaling + scale-in protection, DynamoDB lock 테이블 Ensure 및 deploy 순서·게이트 수렴.
- **Step H~I:** 락 heartbeat/fencing, 재생성 옵션(-RecreateNetwork/-RecreateBatch 등)은 추후 구현.

---

## 변경 파일 목록

| 파일 | 변경 이유 |
|------|-----------|
| `docs/00-SSOT/v4/params.yaml` | Final Design v1.0 반영: 2-tier 네트워크(public/private 서브넷), NAT/ALB 플래그, API ALB·asg 1/2/1, Workers min=1 max=10 desired=1·scale-in protection·cooldown, Batch minvCpus/maxvCpus/instanceType, DynamoDB lock 테이블, ecr.immutableTagRequired. |
| `docs/00-SSOT/v4/ARCHITECTURE.md` | 신규. A to Z 최종 설계(네트워크, 컴퓨팅, 데이터, 배포 순서, 게이트, 멱등 규칙). |
| `docs/00-SSOT/v4/reports/STEP-A-DISCOVERY-CURRENT-STATE.md` | 신규. 현재 인프라 코드 기준 정리, Batch/ASG 서브넷·SG, Describe 명령 안내, 출구 없음 가설 3개. |
| `scripts/v4/core/ssot.ps1` | networkPublicSubnets/networkPrivateSubnets, PrivateSubnets, NatEnabled, AlbEnabled, SecurityGroupApp/Batch/Data, DeployLockParamName, EcrImmutableTagRequired, API alb/targetGroup/healthPath·asg 1/2/1, Workers scaleInProtection·cooldown, VideoCE min/max/instanceType, DynamoDB lock 테이블, SSOT_EIP 비어 있을 수 있음. |
| `scripts/v4/core/guard.ps1` | 변경 없음. DeployLockParamName은 ssot에서 설정. |
| `scripts/v4/deploy.ps1` | EcrImmutableTagRequired 시 EcrRepoUri 미지정이면 throw, FD1 순서(네트워크 → RDS/Redis → SSM/ECR → DynamoDB → Workers → Batch → API → Build)로 Ensure 호출. |
| `scripts/v4/resources/network.ps1` | 2-tier VPC/서브넷/IGW/NAT/RT/SG(academy-v4-*)를 Ensure. VpcId/서브넷 비어 있으면 생성 후 태그 기반 발견. |
| `scripts/v4/resources/api.ps1` | 서브넷: PrivateSubnets 우선. EIP 제거, ALB Target Group 연동, ApiBaseUrl(ALB DNS) 있을 때 `/health` 200까지 대기. |
| `scripts/v4/resources/alb.ps1` | ALB + Target Group + Listener Ensure (academy-v4-api-alb / academy-v4-api-tg). |
| `scripts/v4/resources/asg_ai.ps1` | 서브넷: PrivateSubnets 우선. min=1 max=10 clamp, scale-in protection, SQS backlog 기반 스케일 정책(임계값 params화). |
| `scripts/v4/resources/asg_messaging.ps1` | 동일 (Messaging ASG). |
| `scripts/v4/resources/batch.ps1` | Video/Ops CE/Queue Ensure가 academy-v4-* 이름으로 동작. PrivateSubnets 우선, describe 기반 Wait 사용. |
| `scripts/v4/resources/jobdef.ps1` | EcrImmutableTagRequired이고 EcrRepoUri 없으면 throw. Drift 있을 때만 새 revision 등록. |
| `scripts/v4/resources/rds.ps1` | academy-v4-db RDS 인스턴스 Ensure (Private Subnet 2개, sg-data, SSM 비밀번호, available까지 대기). |
| `scripts/v4/resources/redis.ps1` | academy-v4-redis replication group Ensure (Private Subnet 2개, sg-data, available + primary endpoint까지 대기). |
| `scripts/v4/resources/dynamodb.ps1` | academy-v4-video-job-lock 테이블 Ensure (PK=videoId, TTL=ttl, PAY_PER_REQUEST). |
| `scripts/v4/templates/batch/video_compute_env.json` | minvCpus/maxvCpus/instanceTypes·subnets를 PLACEHOLDER로 변경. |
| `scripts/v4/templates/batch/ops_compute_env.json` | subnets를 PLACEHOLDER_SUBNETS로 변경. |

---

## 실행 커맨드 예시

### 로컬 (Plan만, AWS 변경 없음)

```powershell
pwsh -File scripts/v4/deploy.ps1 -Env prod -Plan
```

### 로컬 (Apply, ECR 이미지 태그 필수)

```powershell
$uri = "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:20250227-abc123"
pwsh -File scripts/v4/deploy.ps1 -Env prod -EcrRepoUri $uri
```

### CI (GitHub Actions)

- 워크플로에서 이미지 빌드 후 `EcrRepoUri` 출력을 받아 deploy에 전달.
- 예: `pwsh -File scripts/v4/deploy.ps1 -Env prod -Ci -EcrRepoUri "${{ needs.build.outputs.ecr_uri }}"`

---

## 위험 작업 / 가드

- **PruneLegacy / PurgeAndRecreate:** 리소스 삭제 포함. 반드시 `-Plan`으로 후보 확인 후 실행. 위험 작업에는 추가 Confirm 프롬프트 권장(현재는 플래그만).
- **params.yaml의 vpcId/서브넷 비어 있음:** 신규 구축 시 네트워크 Ensure(Step C) 구현 후 생성·채움. 현재는 비어 있으면 검증 스킵.

---

## 미구현 (남은 Step)

- **Step F:** Batch 고정 sleep 제거, describe 기반 wait 100% 통합, 게이트 정리(부분 구현 상태).
- **Step H:** deploy-lock heartbeat + fencing token 강화.
- **Step I:** -RecreateNetwork / -RecreateCompute / -RecreateBatch 옵션, 딸깍 배포 UX 정리.
