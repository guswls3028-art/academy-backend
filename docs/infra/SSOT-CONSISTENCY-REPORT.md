# SSOT ↔ AWS 실제 상태 정합성 검사

**검사일시:** 2026-03-06  
**리전:** ap-northeast-2  
**SSOT:** docs/00-SSOT/v1/params.yaml

---

## 1. 현재 AWS 리소스 상태 (실제)

### 1.1 데이터 계층 (보호·유지됨)

| 리소스 | SSOT 기대 | 실제 상태 | 정합성 |
|--------|-----------|-----------|--------|
| RDS | academy-db | academy-db (available) | ✅ |
| RDS VPC | vpc-0831a2484f9b114c2 | vpc-0831a2484f9b114c2 | ✅ |
| RDS SG | academy-rds | sg-06cfb1f23372e2597 (academy-rds) | ✅ |
| Redis | academy-v1-redis | academy-v1-redis (available) | ✅ |
| Redis (legacy) | - | academy-redis (available) | ⚠️ SSOT 외, 유지 |
| DynamoDB | academy-v1-video-job-lock, academy-v1-video-upload-checkpoints | 동일 2개 존재 | ✅ |
| SQS | academy-v1-ai-queue, academy-v1-messaging-queue + DLQ | 4개 존재 | ✅ |

### 1.2 네트워크 (VPC vpc-0831a2484f9b114c2)

| 항목 | SSOT | 실제 | 정합성 |
|------|------|------|--------|
| VPC CIDR | 172.30.0.0/16 | 172.30.0.0/16 | ✅ |
| Public-a | 172.30.0.0/24 | subnet-07a8427d3306ce910 (academy-v1-public-a) | ✅ |
| Public-b | 172.30.2.0/24 | subnet-0548571ac21b3bbf3 (academy-v1-public-b) | ✅ |
| Private-a | 172.30.1.0/24 | subnet-09231ed7ecf59cfa4 (academy-v1-private-a) | ✅ |
| Private-b | 172.30.3.0/24 | subnet-049e711f41fdff71b (academy-v1-private-b) | ✅ |
| RDS subnet group | academy-v1-db-subnets | 존재 (private-a, private-b) | ✅ |
| Redis subnet group | academy-v1-redis-subnets | 존재 (private-a, private-b) | ✅ |

### 1.3 Security Groups (vpc-0831a2484f9b114c2)

| SSOT 기대 | 실제 | 비고 |
|-----------|------|------|
| academy-v1-sg-app | **없음** | Ensure-Network가 생성 예정 |
| academy-v1-sg-batch | **없음** | Ensure-Network가 생성 예정 |
| academy-v1-sg-data | sg-0f04876abb91d1606 | Redis용, 유지됨 |
| academy-rds | sg-06cfb1f23372e2597 | RDS용 |
| default | sg-0405c1afe368b4e6b | - |
| academy-redis-sg | sg-0f4069135b6215cad | academy-redis용 (SSOT 외) |

### 1.4 컴퓨트 (삭제됨·재생성 대상)

| 리소스 | SSOT 기대 | 실제 | 비고 |
|--------|-----------|------|------|
| EC2 | API, AI, Messaging 워커 | 0개 | 배포 시 생성 |
| ASG | 3개 (api, ai-worker, messaging-worker) | 0개 | 배포 시 생성 |
| ALB | academy-v1-api-alb | 없음 | 배포 시 생성 |
| Target Group | academy-v1-api-tg | 없음 | 배포 시 생성 |
| Launch Template | academy-v1-api-lt 등 | 없음 | 배포 시 생성 |
| Batch CE | video-batch, video-ops, (video-long) | 0개 | 배포 시 생성 |
| Batch Queue | video-batch-queue, ops-queue, (video-long-queue) | 0개 | 배포 시 생성 |
| Batch JobDef | video-batch-jobdef, ops-* | 0개 | 배포 시 생성 |
| EventBridge | reconcile, scan-stuck | 0개 | 배포 시 생성 |
| VPC Endpoints | ecr.api, ecr.dkr, s3 | S3만 존재 | ecr.api, ecr.dkr 배포 시 생성 |

### 1.5 의존성 (배포 전 확인)

| 항목 | SSOT | 실제 | 정합성 |
|------|------|------|--------|
| SSM /academy/workers/env | 필수 | 존재 | ✅ |
| SSM /academy/api/env | 필수 | 존재 | ✅ |
| SSM /academy/rds/master_password | RDS용 | (미확인, Bootstrap에서 생성) | ⚠️ |
| ECR academy-api | 필수 | 존재 | ✅ |
| ECR academy-ai-worker-cpu | 필수 | 존재 | ✅ |
| ECR academy-messaging-worker | 필수 | 존재 | ✅ |
| ECR academy-video-worker | 필수 | 존재 | ✅ |

---

## 2. RDS ↔ sg-data 연결

- **현재:** RDS는 `sg-06cfb1f23372e2597`(academy-rds)만 연결됨.
- **SSOT 설계:** `Ensure-RDSSecurityGroup`가 `sg-data`를 RDS에 추가함.
- **sg-data 규칙:** 5432/6379 from sg-app, sg-batch. (리셋 시 제거됨 → `Ensure-SecurityGroups`가 sg-app/sg-batch 생성 후 규칙 재추가)
- **정합성:** 배포 시 `Ensure-Network` → `Ensure-RDSSecurityGroup` 순으로 실행되면 정상 동작.

---

## 3. 복잡도 관점

### 3.1 현재 SSOT/배포 구조

| 영역 | 리소스 수 | 복잡도 | 비고 |
|------|-----------|--------|------|
| API | 1 ASG, 1 LT, 1 ALB, 1 TG | 낮음 | min=1, max=2 |
| AI Worker | 1 ASG, 1 LT, SQS | 낮음 | min=1, max=5 |
| Messaging Worker | 1 ASG, 1 LT, SQS | 낮음 | min=1, max=3 |
| Video Batch Standard | 1 CE, 1 Queue, 1 JobDef | 중간 | 3시간 이하, Spot |
| Video Batch Long | 1 CE, 1 Queue, 1 JobDef | 중간 | 3시간 초과, On-Demand |
| Video Ops | 1 CE, 1 Queue, 3 JobDef, 2 EventBridge | 중간 | reconcile, scanstuck, netprobe |

### 3.2 철학 유지·안정화 포인트

1. **1동영상=1워커=1작업:** Video Batch JobDef 설계 유지.
2. **빌드 서버 0대:** GitHub Actions OIDC만 빌드·푸시.
3. **원테이크 배포:** Bootstrap으로 SSM/SQS/ECR resolve.
4. **NAT 미사용:** Public subnet 기반, 비용 절감.
5. **Strict 검증:** EcrRepoUri, SQS, RDS password 등 사전 검증.

### 3.3 안정 배포를 위한 권장 사항

| 항목 | 권장 | 이유 |
|------|------|------|
| Video Long CE/Queue | 초기에는 비활성화 가능 | 3시간 이하만 사용 시 표준 CE만으로 충분 |
| Netprobe | `-SkipNetprobe`로 첫 배포 시 생략 가능 | Cold start 5~10분 대기 회피 |
| ECR 이미지 | `:latest` 또는 immutable tag 사전 푸시 | Bootstrap EcrRepoUri resolve 실패 방지 |
| RDS master password | SSM `/academy/rds/master_password` 사전 생성 | Strict 검증 통과 |

---

## 4. 배포 실행 시 예상 흐름

```
1. Preflight: AWS, VPC, SSM, ECR ✅
2. Ensure-Network: 서브넷 유지, sg-app/sg-batch 생성, sg-data 규칙 추가, VPC Endpoints(ecr.api, ecr.dkr) 생성
3. Bootstrap: SQS/SSM/ECR resolve (이미 존재하면 스킵)
4. Confirm-RDSState, Confirm-RedisState
5. Ensure-RDSSecurityGroup: sg-data를 RDS에 추가
6. Ensure-ASGMessaging, Ensure-ASGAi
7. Ensure-VideoCE, Ensure-OpsCE, (Ensure-VideoLongCE)
8. Ensure-VideoQueue, Ensure-OpsQueue, (Ensure-VideoLongQueue)
9. Ensure-VideoJobDef, Ensure-OpsJobDef*, Ensure-EventBridgeRules
10. Ensure-ALBStack, Ensure-API
11. Netprobe (선택)
```

---

## 5. 정합성 요약

| 구분 | 상태 | 비고 |
|------|------|------|
| 데이터 계층 | ✅ 정합 | RDS, Redis, DynamoDB, SQS SSOT와 일치 |
| 네트워크 | ✅ 정합 | VPC, 서브넷, CIDR 일치 |
| SG | ⚠️ sg-app/sg-batch 없음 | 배포 시 Ensure-Network가 생성 |
| 컴퓨트 | ❌ 없음 | 배포로 전부 생성 |
| 의존성 | ✅ 정합 | SSM, ECR 존재 |

**결론:** 현재 AWS 상태는 SSOT와 정합되어 있으며, `deploy.ps1` 실행 시 필요한 리소스가 순서대로 생성·연결될 구조이다. ECR 이미지와 RDS master password SSM만 확인하면 배포 가능.
