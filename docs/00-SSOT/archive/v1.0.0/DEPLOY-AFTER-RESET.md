# 인프라 리셋 후 재배포 가이드

**목표:** API 서버, AI/Messaging 워커 ASG, Video Batch(3시간 영상, 1동영상=1워커=1작업)를 최소 복잡도로 배포.

---

## 전제 조건

- **보호 리소스 유지됨:** RDS(academy-db), Redis(academy-v1-redis), DynamoDB, SQS
- **params.yaml SSOT:** `securityGroupApp`, `securityGroupBatch`, `securityGroupData`, `api.securityGroupId`는 **비어 있음** (Ensure-Network가 이름으로 찾거나 생성)
- **ECR 이미지:** GitHub Actions로 빌드·푸시 완료 후 배포

---

## 배포 순서

### 1. ECR 이미지 확인

```powershell
aws ecr describe-images --repository-name academy-api --region ap-northeast-2 --query "imageDetails[*].imageTags" --output json
aws ecr describe-images --repository-name academy-ai-worker-cpu --region ap-northeast-2 --query "imageDetails[*].imageTags" --output json
aws ecr describe-images --repository-name academy-messaging-worker --region ap-northeast-2 --query "imageDetails[*].imageTags" --output json
aws ecr describe-images --repository-name academy-video-worker --region ap-northeast-2 --query "imageDetails[*].imageTags" --output json
```

이미지가 없으면 GitHub Actions로 빌드·푸시 먼저 수행.

### 2. .env 준비

루트 `.env`에 다음이 있어야 함:

- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION=ap-northeast-2`
- `DB_HOST`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_PORT`
- `REDIS_HOST`, `REDIS_PORT`
- `R2_ACCESS_KEY`, `R2_SECRET_KEY`, `R2_ENDPOINT`, `R2_VIDEO_BUCKET`
- `API_BASE_URL`, `INTERNAL_WORKER_TOKEN`

### 3. 배포 실행

```powershell
cd C:\academy
$env:AWS_ACCESS_KEY_ID = "..."   # .env에서 로드
$env:AWS_SECRET_ACCESS_KEY = "..."
$env:AWS_DEFAULT_REGION = "ap-northeast-2"

pwsh -File scripts/v1/deploy.ps1 -Env prod -AwsProfile default
# 또는 env가 이미 설정되어 있으면:
pwsh -File scripts/v1/deploy.ps1 -Env prod
```

- **예상 소요:** 20~25분 (API health 대기 + Netprobe cold start 포함)
- **타임아웃:** CI/터미널 30분 이상 권장

### 4. 배포 후 확인

| 항목 | 확인 방법 |
|------|-----------|
| API /healthz | ALB DNS 또는 api.&lt;domain&gt; 호출 |
| AI/Messaging ASG | `aws autoscaling describe-auto-scaling-groups --region ap-northeast-2` |
| Video Batch CE/Queue | Batch 콘솔 또는 `aws batch describe-compute-environments` |
| RDS/Redis | `Confirm-RDSState`, `Confirm-RedisState` (배포 스크립트 내) |

---

## 실패 시 체크리스트

1. **Preflight FAIL: SSM missing**  
   → Bootstrap이 SSM `/academy/workers/env` 생성. `.env`에 필수 키 있는지 확인.

2. **Preflight FAIL: ECR repo not found**  
   → GitHub Actions로 이미지 푸시 후 재시도.

3. **Strict: EcrRepoUri not set**  
   → Bootstrap이 ECR 이미지 resolve 실패. `-EcrRepoUri <uri>` 전달하거나, ECR에 이미지 존재 확인.

4. **API SG required / InvalidGroup.NotFound**  
   → params.yaml의 `securityGroupApp`, `api.securityGroupId`가 비어 있는지 확인. Ensure-Network가 academy-v1-sg-app을 생성함.

5. **RDS 연결 불가**  
   → RDS SG(academy-rds)에 sg-data가 연결되어 있고, sg-data가 5432 from sg-app 허용하는지 확인. `Ensure-RDSSecurityGroup`가 sg-data를 RDS에 추가함.

---

## 아키텍처 (최소 구성)

```
[ALB] → [API ASG 1대] ─┬→ RDS (academy-db)
                      ├→ Redis (academy-v1-redis)
                      └→ SQS (ai, messaging)

[AI Worker ASG 1대]    → SQS academy-v1-ai-queue
[Messaging Worker ASG 1대] → SQS academy-v1-messaging-queue

[Video Batch CE]      → 1동영상=1Job=1워커, jobTimeout 6h (3시간 영상)
```
