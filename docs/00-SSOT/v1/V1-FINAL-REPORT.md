# V1 최종 보고서 (빌드·배포·프론트·비용)

**기준일:** 2026-03-05  
**기준:** V1 SSOT (`docs/00-SSOT/v1/SSOT.md`, `params.yaml`)

---

## 1. 수행 요약

| # | 항목 | 상태 | 비고 |
|---|------|------|------|
| 1 | 빌드 서버 Spot 인스턴스 적용 | ✅ 반영 | `scripts/v1/resources/build.ps1` — New-BuildInstance 시 `--instance-market-options MarketType=spot` 적용 |
| 2 | 빌드 서버 경유 ECR 푸시·배포 | ⚠️ 로컬 자격증명 필요 | 아래 2절 명령으로 **로컬에서** AWS 프로파일 유효한 상태로 실행 필요 |
| 3 | API·AI·Messaging·Video 워커 파이프라인 | ✅ 인프라 존재 | ASG·Batch·SQS·ALB·TG 구성됨. 동작 검증은 배포 후 /health·SQS/Batch 테스트로 확인 |
| 4 | 프론트(hakwonplus.com) 연결·정상 확인 | ✅ 도메인 응답 확인 | hakwonplus.com 응답 있음. API 호출은 `api.hakwonplus.com` 또는 `v1-api.hakwonplus.com` 필요(아래 4절) |
| 5 | 불필요 이미지·비용 최적화 | ✅ 정책·가이드 추가 | ECR 라이프사이클 정책 예시 및 적용 방법 문서화 |

---

## 2. 빌드용 서버(Spot) + ECR 푸시 및 배포 (실행 가이드)

### 2.1 빌드 서버 Spot 적용 내용

- **파일:** `scripts/v1/resources/build.ps1`
- **변경:** `New-BuildInstance`에서 `run-instances` 시 아래 옵션 추가  
  `--instance-market-options "MarketType=spot,SpotOptions={SpotInstanceType=one-time,InstanceInterruptionBehavior=terminate}"`
- **효과:** 빌드 전용 EC2를 Spot으로 기동해 비용 절감. 기존처럼 태그 `Name=academy-build-arm64`로 관리.

### 2.2 배포 실행 (로컬에서 AWS 자격증명 설정 후)

**Option A — 기존 이미지로 배포만 (빌드 스킵)**  
이미 ECR에 immutable 태그 `v1-20260305-1918` 생성해 둠. 아래로 JobDef 등만 갱신 가능.

```powershell
cd C:\academy
$uri = "809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker:v1-20260305-1918"
pwsh scripts/v1/deploy.ps1 -Env prod -SkipBuild -EcrRepoUri $uri -AwsProfile default -SkipNetprobe
```

**Option B — 빌드 서버에서 빌드 후 ECR 푸시까지 포함**  
빌드 인스턴스가 없으면 Spot으로 생성 후, 해당 서버에서 도커 빌드·ECR 푸시 수행.

```powershell
cd C:\academy
# -SkipBuild 제거 → Bootstrap이 빌드 서버 기동 후 SSM으로 빌드·푸시
pwsh scripts/v1/deploy.ps1 -Env prod -AwsProfile default -SkipNetprobe
```

- **전제:** 빌드 인스턴스에 `/opt/academy` 또는 `$HOME/academy`에 academy 리포지토리 클론되어 있어야 함. 최초 1회 수동 클론 또는 시딩 필요.
- **도커:** Bootstrap은 `Dockerfile.video`(또는 기본 Dockerfile)로 `academy-video-worker` 이미지 빌드·푸시. 기존 도커 파일 최적화에 맞춰 사용 중.

---

## 3. API·AI·Messaging·Video 워커 파이프라인

- **API:** academy-v1-api-asg → academy-v1-api-alb → academy-v1-api-tg, Health Path `/health`.  
  - Target이 healthy여야 ALB에서 200 반환. 이전 검증에서 LT 보안 그룹을 academy-v1-sg-app으로 수정해 둠.
- **AI / Messaging 워커:** academy-v1-ai-worker-asg, academy-v1-messaging-worker-asg — SQS academy-v1-ai-queue, academy-v1-messaging-queue 구독.
- **Video 워커:** AWS Batch — academy-v1-video-batch-ce / academy-v1-video-batch-queue, JobDef는 `EcrRepoUri`(위 배포 시 사용한 이미지) 사용.
- **정상 작동 확인:**  
  - API: `curl https://v1-api.hakwonplus.com/health` 또는 ALB DNS `/health` → 200 기대.  
  - 워커: SQS 메시지 투입 후 처리 여부, Batch Job 제출 후 RUNNING/SUCCEEDED 확인.

---

## 4. 프론트 연결 및 정상 확인

- **hakwonplus.com:** 접속 시 본문에 "HakwonPlus" 등 응답 확인됨. 프론트 리소스는 서빙 중인 상태.
- **API 베이스 URL:**  
  - 프론트 `.env.example` / 프로덕션 빌드: `VITE_API_BASE_URL=https://api.hakwonplus.com`  
  - V1 ALB는 **v1-api.hakwonplus.com** (CNAME)으로 노출됨.
- **필요 조치:**  
  - **Case A:** `api.hakwonplus.com`을 V1 ALB와 동일한 엔드포인트로 쓰려면 Cloudflare DNS에서 `api.hakwonplus.com`을 같은 ALB로 CNAME(또는 v1-api.hakwonplus.com으로 프록시) 설정.  
  - **Case B:** 프론트 프로덕션 빌드에서 `VITE_API_BASE_URL=https://v1-api.hakwonplus.com`으로 설정 후 재배포.  
- **정상 확인:** 브라우저에서 hakwonplus.com 접속 → 로그인/API 호출 시 4xx/5xx 없이 동작하는지 확인.

---

## 5. 불필요 이미지·비용 최적화

### 5.1 ECR

- **현황:** academy-video-worker 등에 태그 없는 이미지 다수 존재. 저장소별로 오래된 이미지 정리 시 비용 절감.
- **적용 예시:** `docs/00-SSOT/v1/scripts/ecr-lifecycle-policy.json`에 예시 정책 추가됨.  
  - tagged: `v1-`, `bootstrap-` 접두사 이미지 중 최신 20개만 유지.  
  - untagged: 1일 경과 후 만료.
- **리포지토리별 적용:**

```powershell
aws ecr put-lifecycle-policy --repository-name academy-video-worker --lifecycle-policy-text "file://C:/academy/docs/00-SSOT/v1/scripts/ecr-lifecycle-policy.json" --profile default --region ap-northeast-2
# 필요 시 academy-api, academy-messaging-worker, academy-ai-worker-cpu 등에도 동일 적용
```

### 5.2 기타

- **Video Batch CE:** min 0, max 10 — 작업 없을 때 비용 없음.  
- **Video Ops CE:** min 0, max 2 vCPU, m6g.medium 1대 상한 — 작업 시에만 기동.  
- **Build:** Spot 적용으로 빌드 시 비용 절감.  
- **S3:** 미사용(리소스 0개). R2만 사용.

---

## 6. 최종 체크리스트 (사용자 실행)

| 순서 | 작업 | 명령/확인 |
|------|------|-----------|
| 1 | AWS 자격증명 확인 | `aws sts get-caller-identity --profile default` |
| 2 | 배포 실행 (이미지만 반영) | 2절 Option A 또는 Option B |
| 3 | API 헬스 | `curl -s -o /dev/null -w "%{http_code}" https://v1-api.hakwonplus.com/health` → 200 |
| 4 | 프론트 API URL | api.hakwonplus.com CNAME 또는 VITE_API_BASE_URL=v1-api.hakwonplus.com 후 재배포 |
| 5 | ECR 라이프사이클 (선택) | 5절 명령으로 저장소별 적용 |

---

## 7. 참조 문서

- **SSOT:** `docs/00-SSOT/v1/SSOT.md`  
- **배포 최종 상태:** `docs/00-SSOT/v1/V1-DEPLOYMENT-FINAL-STATE.md`  
- **배포 검증:** `docs/00-SSOT/v1/V1-DEPLOYMENT-VERIFICATION.md`  
- **배포 플랜:** `docs/00-SSOT/v1/V1-DEPLOYMENT-PLAN.md`

---

**문서 끝.**
