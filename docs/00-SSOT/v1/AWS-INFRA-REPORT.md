# AWS 인프라 현황 보고서

**조회 일시:** 2026-03-05  
**계정 ID:** 809466760795  
**리전:** ap-northeast-2 (서울)

---

## 1. 요약

| 구분 | 실제 설정 현황 |
|------|----------------|
| **RDS (PostgreSQL)** | 1개 인스턴스 (`academy-db`) |
| **DynamoDB** | 테이블 없음 |
| **EC2** | 2개 (API 1 running, Build 1 stopped) |
| **AWS Batch** | CE 2개, Job Queue 2개 (1개 CE INVALID) |
| **ECR** | 4개 리포지토리 |
| **Lambda** | 2개 함수 |
| **VPC** | 4개 |
| **S3** | 버킷 없음 (정책 준수) |
| **Auto Scaling Group** | 없음 |
| **Load Balancer** | 없음 |

---

## 2. RDS (PostgreSQL)

| 항목 | 값 |
|------|-----|
| **DB 식별자** | academy-db |
| **엔진** | postgres 15.16 |
| **인스턴스 클래스** | db.t4g.medium |
| **상태** | available |
| **엔드포인트** | academy-db.cbm4oqigwl80.ap-northeast-2.rds.amazonaws.com:5432 |
| **스토리지** | 20 GB gp3, 3,000 IOPS, 125 MB/s throughput, Max 100 GB 자동 확장 |
| **AZ** | ap-northeast-2b (Single-AZ) |
| **Multi-AZ** | false |
| **퍼블릭 접근** | true |
| **암호화** | 스토리지·Performance Insights 모두 KMS 암호화 |
| **백업** | 7일 보존, 16:18–16:48 UTC |
| **유지 보수** | 목 20:20–20:50 UTC (KST 목 05:20–05:50) |
| **Performance Insights** | 활성화, 7일 보존 |
| **보안 그룹** | sg-06cfb1f23372e2597 (academy-rds) |
| **서브넷 그룹** | default-vpc-0831a2484f9b114c2 (VPC 4개 AZ 서브넷) |
| **Deletion Protection** | false |

---

## 3. DynamoDB

- **테이블:** 없음 (현재 계정·리전에 테이블 미생성)
- SSOT v1의 `academy-v1-video-job-lock` 등은 아직 생성 전으로 보임.

---

## 4. EC2 인스턴스

### 4.1 academy-api (실행 중)

| 항목 | 값 |
|------|-----|
| **인스턴스 ID** | i-0c8ae616abf345fd1 |
| **이름** | academy-api |
| **타입** | t4g.small (ARM) |
| **상태** | running |
| **AMI** | ami-0b7324d721edeadc7 |
| **AZ** | ap-northeast-2d |
| **퍼블릭 IP** | 15.165.147.157 (EIP 연결됨) |
| **프라이빗 IP** | 172.30.3.142 |
| **VPC** | vpc-0831a2484f9b114c2 |
| **보안 그룹** | academy-api-sg (sg-0051cc8f79c04b058) |
| **IAM 프로파일** | academy-ec2-role |
| **키 페어** | backend-api-key |

### 4.2 academy-build-arm64 (중지됨)

| 항목 | 값 |
|------|-----|
| **인스턴스 ID** | i-0133290c3502844ab |
| **이름** | academy-build-arm64 |
| **타입** | t4g.medium (ARM) |
| **상태** | stopped |
| **AZ** | ap-northeast-2a |
| **프라이빗 IP** | 172.30.0.184 (퍼블릭 IP 없음) |
| **VPC** | vpc-0831a2484f9b114c2 |
| **보안 그룹** | academy-worker-sg (sg-02692600fbf8e26f7) |
| **IAM 프로파일** | academy-ec2-role |
| **중지 사유** | User initiated (2026-02-28 17:16:48 GMT) |

---

## 5. AWS Batch

### 5.1 컴퓨트 환경 (CE)

| CE 이름 | 상태 | 인스턴스 타입 | min/max vCPU | 비고 |
|---------|------|----------------|--------------|------|
| academy-video-batch-ce-final | **VALID** | c6g.large | 0 / 32 | 정상 |
| academy-video-ops-ce | **INVALID** | c6g.large | 0 / 2 | 인스턴스가 ECS 클러스터에 조인하지 못함 (VPC/서브넷·IAM·AMI 등 점검 필요) |

- 공통: ECS AL2023, 서브넷 4개 AZ, academy-video-batch-sg, academy-batch-ecs-instance-profile, academy-batch-service-role

### 5.2 Job Queue

| Queue 이름 | 상태 | 연결 CE |
|------------|------|----------|
| academy-video-batch-queue | VALID | academy-video-batch-ce-final |
| academy-video-ops-queue | VALID | academy-video-ops-ce (CE가 INVALID이므로 작업 실행 불가) |

---

## 6. ECR 리포지토리

| 리포지토리 | URI |
|------------|-----|
| academy-base | 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-base |
| academy-video-worker | 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-video-worker |
| academy-messaging-worker | 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-messaging-worker |
| academy-ai-worker-cpu | 809466760795.dkr.ecr.ap-northeast-2.amazonaws.com/academy-ai-worker-cpu |

- 이미지 태그: MUTABLE, 푸시 시 스캔 비활성화, 저장 암호화 AES256.

---

## 7. Lambda

| 함수명 | 런타임 | 용도 추정 |
|--------|--------|-----------|
| academy-worker-queue-depth-metric | python3.11 | 워커 큐 깊이 메트릭 |
| academy-worker-autoscale | python3.11 | 워커 오토스케일 |

---

## 8. VPC

| VPC ID | 이름 | CIDR | 비고 |
|--------|------|------|------|
| vpc-0831a2484f9b114c2 | (없음) | 172.30.0.0/16 | RDS·EC2 API/빌드·Batch·대부분 SG 사용 |
| vpc-0b89e02241aae4b0e | (Default) | 172.31.0.0/16 | 기본 VPC |
| vpc-00fb37f7f4bc98385 | academy-v4-vpc | 10.0.0.0/16 | Project: academy |
| vpc-009e3ea6265c7a203 | academy-lambda-metric-vpc | 10.1.0.0/16 | Lambda 메트릭용 |

---

## 9. 보안 그룹 (academy 관련, vpc-0831a2484f9b114c2)

| SG ID | 이름 | 용도 추정 |
|-------|------|-----------|
| sg-0051cc8f79c04b058 | academy-api-sg | API EC2 |
| sg-02692600fbf8e26f7 | academy-worker-sg | 빌드/워커 EC2 |
| sg-06cfb1f23372e2597 | academy-rds | RDS |
| sg-011ed1d9eb4a65b8f | academy-video-batch-sg | Batch CE |
| sg-0944a30cabd0c022e | academy-lambda-endpoint-sg | Lambda 엔드포인트 |
| sg-0ff11f1b511861447 | academy-lambda-internal-sg | Lambda 내부 |
| sg-0caaa6c43e12758e6 | academy-lambda-video-sg | Lambda 비디오 |
| sg-0f4069135b6215cad | academy-redis-sg | Redis (ElastiCache는 미조회 시 존재 가능) |

---

## 10. IAM 역할 (academy)

- academy-batch-ecs-instance-role
- academy-batch-ecs-task-execution-role
- academy-batch-service-role
- academy-eventbridge-batch-video-role
- academy-video-batch-job-role  

(EC2 인스턴스 프로파일: academy-ec2-role)

---

## 11. 기타

- **EC2 키 페어:** backend-api-key, ai-worker-key, message-key, video-worker-key
- **Elastic IP:** 4개 (API EC2 1개 연결, academy-v4-nat-eip 1개, RDS 1개, 미연결 1개)
- **KMS:** RDS용 alias/aws/rds 사용 중
- **S3:** 버킷 없음 → .cursorrules 정책(S3 사용 금지) 준수

---

## 12. SSOT v1 대비 갭 (참고)

| SSOT v1 리소스 | 현재 AWS 상태 |
|----------------|----------------|
| academy-v1-api-asg, academy-v1-api-alb | ASG/ALB 없음. EC2 1대만 수동 운영 |
| academy-v1-db | academy-db로 존재 (이름만 상이) |
| academy-v1-redis | SG만 존재, ElastiCache 미조회 |
| academy-v1-video-job-lock (DynamoDB) | 테이블 없음 |
| academy-v1-* Batch/EventBridge | academy-video-* 이름으로 일부 존재, CE 1개 INVALID |
| v1 네이밍 (academy-v1-*) | 실제는 academy-* 또는 academy-v4-* 혼용 |

---

**보고서 끝.**  
추가로 ElastiCache(Redis), EventBridge 규칙, Batch Job Definition 등 세부 조회가 필요하면 요청 시 반영 가능.
