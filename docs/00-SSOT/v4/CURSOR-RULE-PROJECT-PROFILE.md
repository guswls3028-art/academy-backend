# Cursor 룰용 프로젝트·인프라 프로필

**작성 목적:** Cursor Rule 생성 시 참조용 단일 프로필  
**기준:** academy + academyfront 코드베이스 분석 결과 (SSOT v4, params.yaml, 스크립트, 워크플로우)

---

## 1️⃣ 프로젝트 기본 정보

### 1. 프로젝트 이름

| 채움 | 값 | 근거 |
|------|-----|------|
| ✅ | **Academy** (백엔드 레포: academy, 프론트: academyfront) | README "Academy Backend", "학원 관리 시스템 백엔드", params.yaml `namingPrefix: academy-`, `Project: academy` |

**참고:** 서비스/제품 도메인은 `SITE_URL` 등에서 hakwonplus.com 사용. 레포·인프라 네이밍은 "academy".

---

### 2. 서비스 타입

| 채움 | 선택 | 근거 |
|------|--------|------|
| ✅ | **Mixed** | API( Django/Gunicorn ) + Batch Processing( Video 인코딩, AWS Batch ) + Video Processing( HLS/FFmpeg ) + ML Pipeline( AI Worker Lite/Basic/Premium ) + 메시징( SQS + Solapi SMS/알림톡 ) |

---

### 3. 주요 언어

| 채움 | 선택 | 근거 |
|------|--------|------|
| ✅ | **Mixed** | **Python**( Django, 워커, requirements ), **PowerShell**( scripts/v4 배포·인프라 전부 ), **Node/TypeScript**( academyfront: Vite, React, pnpm ) |

---

### 4. 현재 코드 저장소

| 채움 | 선택 | 근거 |
|------|--------|------|
| ✅ | **GitHub** | `.github/workflows/` 존재( build-and-push-ecr.yml, video_batch_deploy.yml 등 ), push to main 트리거 사용 |

---

## 2️⃣ AWS 환경

### 5. AWS 리전

| 채움 | 값 | 근거 |
|------|-----|------|
| ✅ | **ap-northeast-2** | params.yaml `region: ap-northeast-2`, 워크플로우·SSM·SQS URL 전부 ap-northeast-2 |

---

### 6. 계정 구조

| 채움 | 선택 | 근거 |
|------|--------|------|
| ✅ | **single account** | params.yaml `accountId: "809466760795"` 단일, SQS/ECR URL에 동일 계정 ID만 사용 |

---

### 7. 현재 사용 서비스 (아는 것만 체크)

| 채움 | 서비스 | 사용 여부 | 비고 |
|------|--------|-----------|------|
| ✅ | **EC2** | 사용 | API ASG, 빌드 서버, Messaging/AI Worker ASG (t4g.medium). Batch CE도 EC2 기반. |
| ✅ | **Batch** | 사용 | Video Batch CE/Queue/JobDef, Ops(reconcile/scanstuck/netprobe). |
| ✅ | **SQS** | 사용 | academy-v4-messaging-queue, academy-v4-ai-queue, 스케일 정책 연동. |
| ✅ | **EventBridge** | 사용 | video reconcile/scan-stuck 규칙, 배치 타겟. |
| ✅ | **RDS** | 사용 | academy-v4-db, PostgreSQL 15.16, db.t4g.medium. |
| ✅ | **DynamoDB** | 사용 | academy-v4-video-job-lock (videoId 중복 제출 방지). |
| ✅ | **Redis** | 사용 | ElastiCache academy-v4-redis (cache.t4g.small, 7.1). |
| ✅ | **ECS** | 미사용 | 컴퓨팅은 EC2 ASG + Batch. |
| ✅ | **EKS** | 미사용 | — |
| ✅ | **Lambda** | 레거시만 | v4 메인 플로우에는 없음. archive에 queue_depth Lambda 등. |
| ✅ | **SNS** | 미사용 | 알림은 Solapi. |
| ⚠️ | **S3** | 미사용(주 스토리지) | 오브젝트 스토리지는 **Cloudflare R2** (S3 호환). AWS S3는 미사용. |
| ⚠️ | **CloudFront** | 미사용(AWS) | HLS/CDN은 **Cloudflare CDN** (R2 + Signed URL). AWS CloudFront 아님. |

**정리:** EC2, Batch, SQS, EventBridge, RDS, DynamoDB, Redis 사용. 스토리지/CDN은 Cloudflare(R2 + CDN).

---

## 3️⃣ 워크로드

### 8. 트래픽 타입

| 채움 | 선택 | 근거 |
|------|--------|------|
| ✅ | **mixed** | API 실시간( ALB/API ASG ) + batch heavy( Video Batch, AI 워커 ) + event driven( SQS, EventBridge 규칙 ) |

---

### 9. 최대 동시 작업 수

| 채움 | 값 | 근거 |
|------|-----|------|
| ⚠️ | **[사용자 입력 필요]** | 코드상: Video Batch `maxvCpus=10`, Messaging/AI ASG `maxSize=10`. "동시 작업"을 vCPU 기준인지, Job 수 기준인지, API 동시 접속 기준인지 정의 필요. 예시 답: `10 vCPU (Video Batch)` / `50 jobs` 등 |

**질문:** "최대 동시 작업 수"를 어떤 단위로 정의할까요? (예: Video 동시 인코딩 수, AI 동시 추론 수, API 동시 요청 수 등)

---

### 10. 작업 종류

| 채움 | 값 | 근거 |
|------|-----|------|
| ✅ | **video processing**( HLS 인코딩, R2 업로드 ), **AI inference**( Lite/Basic/Premium 큐 ), **data pipeline**( 메시징·SMS/알림톡 ), **API**( 학원 관리 CRUD·인증 ) | apps/worker, SQS 큐 이름, Batch JobDef |

---

## 4️⃣ 인프라 철학

### 11. 인프라 생성 방식

| 채움 | 선택 | 근거 |
|------|--------|------|
| ✅ | **Script 기반** (PowerShell + AWS CLI) | `scripts/v4/deploy.ps1` 및 resources/*.ps1, AWS CLI/SDK 호출. Terraform/CloudFormation/Pulumi 미사용. |

---

### 12. 인프라 관리 방식

| 채움 | 선택 | 근거 |
|------|--------|------|
| ✅ | **혼합** | **SSOT 문서**( docs/00-SSOT/v4/params.yaml, INFRA-AND-SPECS.md 등 ) + **스크립트**( deploy.ps1, Ensure-* ). IaC 툴은 미사용. |

---

### 13. 원테이크 배포 필요?

| 채움 | 선택 | 근거 |
|------|--------|------|
| ✅ | **YES** | ONE-CLICK-DEPLOY-SCENARIOS.md, deploy.ps1 Bootstrap으로 SSM/SQS/RDS/ECR 자동 준비 후 Ensure 수렴. "한 줄만 실행하면" 원테이크 UX. |

---

## 5️⃣ 비용 전략

### 14. 인스턴스 타입 선호

| 채움 | 선택 | 근거 |
|------|--------|------|
| ✅ | **mixed** | **t4g.medium**: API, 빌드, Messaging ASG, AI ASG. **c6g.large**: Video Batch CE only. (params.yaml, INFRA-AND-SPECS.md) |

---

### 15. 비용 목표

| 채움 | 선택 | 근거 |
|------|--------|------|
| ⚠️ | **[사용자 입력 필요]** | t4g 사용으로 비용 절감 추세이나, Video Batch는 c6g 고정. "최저비용 / 성능우선 / 균형" 중 명시된 정책 없음. |

**질문:** 비용 목표를 "최저비용 / 성능우선 / 균형" 중 어떤 것으로 두시겠습니까?

---

## 6️⃣ CI/CD

### 16. CI 사용

| 채움 | 선택 | 근거 |
|------|--------|------|
| ✅ | **GitHub Actions** | .github/workflows: build-and-push-ecr.yml, build-and-push-ecr-nocache.yml, video_batch_deploy.yml. main push / workflow_dispatch. |

---

### 17. 배포 방식

| 채움 | 선택 | 근거 |
|------|--------|------|
| ✅ | **hybrid** | **auto:** main push 시 ECR 빌드·푸시. **manual/hybrid:** deploy.ps1는 로컬 또는 CI에서 `-EcrRepoUri` 등으로 호출( video_batch_deploy는 빌드 후 deploy.ps1 실행 ). |

---

## 7️⃣ 안정성

### 18. 장애 허용

| 채움 | 선택 | 근거 |
|------|--------|------|
| ⚠️ | **[사용자 입력 필요]** | ASG min=1, RDS 단일 인스턴스, Redis replication group. "low / medium / high" 중 서비스 SLA 목표가 문서에 명시되어 있지 않음. |

**질문:** 장애 허용도를 "low tolerance / medium / high" 중 어떤 것으로 가져갈까요?

---

### 19. 멀티 AZ 필요?

| 채움 | 선택 | 근거 |
|------|--------|------|
| ✅ | **YES** | params.yaml Public 2 + Private 2 서브넷( 2 AZ ), ARCHITECTURE.md "AZ 2개 분산". RDS/Redis 서브넷 그룹도 Multi-AZ 구성. |

---

### 20. 운영 스타일

| 채움 | 선택 | 근거 |
|------|--------|------|
| ⚠️ | **[사용자 입력 필요]** | README "DevOps 팀 또는 프로젝트 관리자에게 문의", SSOT·원테이크 설계로 소수 운영 가정. "solo dev / small team / company scale" 중 선택 필요. |

**질문:** 운영 스타일을 "solo dev / small team / company scale" 중 어떤 것으로 할까요?

---

## 사용자 확인이 필요한 문항 요약

| 번호 | 문항 | 제안/선택지 |
|------|------|-------------|
| **9** | 최대 동시 작업 수 | 단위 정의 필요 (예: 10 vCPU, 50 jobs 등). |
| **15** | 비용 목표 | 최저비용 / 성능우선 / 균형 |
| **18** | 장애 허용 | low tolerance / medium / high |
| **20** | 운영 스타일 | solo dev / small team / company scale |

위 4개만 답해 주시면 프로필을 최종 확정한 뒤 Cursor 룰 초안에 반영할 수 있습니다.

---

## 문서 위치

- **이 프로필:** `docs/00-SSOT/v4/CURSOR-RULE-PROJECT-PROFILE.md`  
- **인프라 스펙:** `docs/00-SSOT/v4/INFRA-AND-SPECS.md`  
- **SSOT:** `docs/00-SSOT/v4/params.yaml`, `docs/00-SSOT/v4/SSOT.md`
