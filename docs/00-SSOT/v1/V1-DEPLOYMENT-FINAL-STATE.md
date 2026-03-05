# V1 배포 최종 상태 (Post-Deploy)

**배포 일시:** 2026-03-05  
**기준:** SSOT v1 (`docs/00-SSOT/v1/SSOT.md`, `params.yaml`)  
**실행:** `scripts/v1/deploy.ps1` (Phase 0~2)

---

## 1. 배포 요약

| Phase | 내용 | 결과 |
|-------|------|------|
| **Phase 0** | params.yaml 검증, RDS=academy-db 반영, VPC/서브넷 태그, AWS 프로파일 default 사용 | 완료 |
| **Phase 1** | 레거시 Batch CE/Queue 수동 삭제, EC2 2대 종료, Lambda 2개 삭제 | 완료 |
| **Phase 2** | V1 Ensure (IAM→Network→RDS→Redis→SSM→ECR→DynamoDB→ASG→Batch→EventBridge→ALB→API→Build) | 완료* |
| **Phase 3** | 검증·문서화 | 본 문서 |

\* API ASG 생성 직후 "SSM agent 대기" 단계에서 인스턴스 ID 전달 버그로 타임아웃 발생. API 인스턴스는 1대 기동 중이며 ALB 타깃 등록 후 `/health` 확인 권장.

---

## 2. AWS 인프라 현황 (V1 기준)

### 2.1 네트워크

| 리소스 | 식별자/이름 | 비고 |
|--------|-------------|------|
| VPC | vpc-0831a2484f9b114c2 (academy-v1-vpc) | 기존 VPC 재사용, CIDR 172.30.0.0/16 |
| 퍼블릭 서브넷 | academy-v1-public-a (subnet-07a8427d3306ce910), academy-v1-public-b (subnet-0548571ac21b3bbf3) | |
| 프라이빗 서브넷 | academy-v1-private-a (subnet-09231ed7ecf59cfa4), academy-v1-private-b (subnet-049e711f41fdff71b) | |
| NAT Gateway | nat-0c3ac9b2cdf785520 | academy-v1-nat |
| 보안 그룹 | academy-v1-sg-app, academy-v1-sg-batch, academy-v1-sg-data | |

### 2.2 RDS

| 항목 | 값 |
|------|-----|
| 식별자 | **academy-db** (기존 유지) |
| 엔드포인트 | academy-db.cbm4oqigwl80.ap-northeast-2.rds.amazonaws.com:5432 |
| 마스터 사용자 | admin97 |
| 비밀번호 | SSM `/academy/rds/master_password` |

### 2.3 Redis (ElastiCache)

| 항목 | 값 |
|------|-----|
| Replication Group | academy-v1-redis |
| Primary | academy-v1-redis.prqwaq.ng.0001.apn2.cache.amazonaws.com:6379 |
| 서브넷 그룹 | academy-v1-redis-subnets |

### 2.4 DynamoDB

| 테이블 | 용도 |
|--------|------|
| academy-v1-video-job-lock | 비디오 Job 락, TTL 속성 ttl |

### 2.5 ECR

| 리포지토리 |
|------------|
| academy-api, academy-video-worker, academy-messaging-worker, academy-ai-worker-cpu, academy-base |

### 2.6 SQS

| 큐 |
|----|
| academy-v1-messaging-queue, academy-v1-ai-queue |

### 2.7 AWS Batch

| 구분 | 이름 | 상태 |
|------|------|------|
| Video CE | academy-v1-video-batch-ce | VALID, ENABLED |
| Ops CE | academy-v1-video-ops-ce | VALID, ENABLED |
| Video Queue | academy-v1-video-batch-queue | ENABLED |
| Ops Queue | academy-v1-video-ops-queue | ENABLED |
| JobDef | academy-v1-video-batch-jobdef, academy-v1-video-ops-reconcile, academy-v1-video-ops-scanstuck, academy-v1-video-ops-netprobe | ACTIVE |

### 2.8 EventBridge

| 규칙 | 용도 |
|------|------|
| academy-v1-reconcile-video-jobs | 비디오 Job 정리 |
| academy-v1-video-scan-stuck-rate | 스캔 스택 모니터링 |

### 2.9 ALB / API

| 항목 | 값 |
|------|-----|
| ALB | academy-v1-api-alb |
| ALB DNS | academy-v1-api-alb-1317506512.ap-northeast-2.elb.amazonaws.com |
| Target Group | academy-v1-api-tg |
| API ASG | academy-v1-api-asg (min=1, max=2, desired=1) |
| API 인스턴스 | i-0a55166b45a6c08cb (1대) |
| Health Path | /health |

### 2.10 워커 ASG

| ASG | min | max | desired | 비고 |
|-----|-----|-----|---------|------|
| academy-v1-messaging-worker-asg | 1 | 10 | 1 | SQS 기반 Application Auto Scaling 미지원으로 min/max만 적용 |
| academy-v1-ai-worker-asg | 1 | 10 | 1 | 동일 |

### 2.11 Build

- 태그: Name=academy-build-arm64 (별도 EC2는 현재 없음, 필요 시 수동 기동 또는 deploy 시 -SkipBuild 해제 후 빌드 서버 사용)

### 2.12 SSM

| 파라미터 |
|----------|
| /academy/api/env, /academy/workers/env, /academy/rds/master_password, /academy/deploy-lock |

---

## 3. Cloudflare (참조)

- **스토리지:** Cloudflare R2 (S3 호환 API). AWS S3 미사용.
- **CDN/DNS/보안:** Cloudflare 사용.
- **설정:** 애플리케이션에서는 SSM `/academy/api/env`, `/academy/workers/env` 및 .env의 R2 버킷·퍼블릭 URL 등으로 참조. Cloudflare 대시보드 설정은 별도 관리.

---

## 4. 수정된 파일 요약

- **docs/00-SSOT/v1/params.yaml**  
  - RDS dbIdentifier=academy-db, masterUsername=admin97  
  - network vpcId, vpcCidr, 서브넷 CIDR (기존 VPC 172.30.0.0/16 기준)  
  - AMI ap-northeast-2 유효 값으로 변경 (ami-0885e191a9bcf28b0)  
  - API/Build instanceProfile=academy-ec2-role  
- **scripts/v1/resources/network.ps1**  
  - 기존 서브넷 라우트 테이블 연동 시 기존 연동 해제 후 재연결 처리  
- **scripts/v1/resources/rds.ps1**  
  - RDS SG 수정 시 "No modifications were requested" 예외 처리  
- **scripts/v1/resources/redis.ps1**  
  - Redis SG 수정 시 동일 예외 처리  
- **scripts/v1/resources/asg_messaging.ps1, asg_ai.ps1**  
  - Application Auto Scaling이 EC2 ASG를 지원하지 않으므로 register-scalable-target 실패 시 스킵·경고  
- **scripts/v1/resources/api.ps1**  
  - IamInstanceProfile 형식 {Name=...} 로 수정  

---

## 5. 권장 후속 작업

1. **API 헬스 확인**  
   - ALB DNS에 대해 `http://academy-v1-api-alb-1317506512.ap-northeast-2.elb.amazonaws.com/health` 호출하여 200 확인.  
   - 타깃 그룹에서 i-0a55166b45a6c08cb healthy 전이면, SSM agent·보안 그룹(8000 포트)·앱 기동 상태 점검.

2. **Build EC2**  
   - 이미지 빌드·ECR 푸시가 필요하면 academy-build-arm64 태그의 EC2를 수동 기동하거나, deploy.ps1에서 -SkipBuild 없이 실행해 빌드 플로우 수행.

3. **Netprobe**  
   - `deploy.ps1` 실행 시 -SkipNetprobe 없이 한 번 실행해 Batch netprobe Job 성공 여부 확인.

4. **Evidence 보고서**  
   - `docs/00-SSOT/v1/reports/audit.latest.md`, `drift.latest.md` 참고.

---

**문서 끝.**
