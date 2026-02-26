# SSOT 리소스 인벤토리 — 리소스/이름/ARN/태그/환경별 값

**역할:** 배포/운영에서 참조하는 리소스의 단일 정의. 모든 문서·스크립트는 이 인벤토리의 이름과 규칙을 따른다.

---

## 공통

| 항목 | 값 | 비고 |
|------|-----|------|
| Region | ap-northeast-2 | |
| VPC (prod) | vpc-0831a2484f9b114c2 | discover_api_network.ps1 또는 actual_state로 확인 |

---

## API 서버

| 리소스 | 이름/식별 | 환경별 값 | 비고 |
|--------|------------|------------|------|
| Elastic IP | 15.165.147.157 | prod 고정 | describe-addresses로 연관 InstanceId 확인 |
| API_BASE_URL | http://15.165.147.157:8000 | prod | 트레일링 슬래시 없음 |
| Instance | (Elastic IP로 연동) | Tag:Name=academy-api 등 | actual_state/api_instance.json |

---

## 빌드 서버

| 리소스 | 이름/식별 | 환경별 값 | 비고 |
|--------|------------|------------|------|
| Instance Tag Name | academy-build-arm64 | | describe-instances filter |
| Subnet | Public Subnet (0.0.0.0/0→IGW) | API와 동일 VPC | SSM·STS 검증 필수 |

---

## Video Batch (AWS Batch)

| 리소스 | 이름 | ARN 식별 | 비고 |
|--------|------|----------|------|
| Compute Environment | academy-video-batch-ce-final | describe-compute-environments로 조회 | MANAGED EC2, c6g.large, min=0 max=32, Public Subnet |
| Job Queue | academy-video-batch-queue | describe-job-queues | CE 단일 연결 |
| Job Definition | academy-video-batch-jobdef | 이름만 사용, revision 하드코딩 금지 | vcpus=2, memory=3072, timeout=14400, retryStrategy.attempts=1, immutable image tag |

---

## Ops Batch (Reconcile / Scan Stuck / Netprobe)

| 리소스 | 이름 | ARN 식별 | 비고 |
|--------|------|----------|------|
| Compute Environment | academy-video-ops-ce | describe-compute-environments | default_arm64, min=0 max=2, Public Subnet |
| Job Queue | academy-video-ops-queue | describe-job-queues | CE 단일 연결 |
| Job Definition (reconcile) | academy-video-ops-reconcile | | timeout 900, vcpus=1, memory=2048, retry=1 |
| Job Definition (scan_stuck) | academy-video-ops-scanstuck | | timeout 900, vcpus=1, memory=2048, retry=1 |
| Job Definition (netprobe) | academy-video-ops-netprobe | | timeout 120, vcpus=1, memory=512, retry=1 |

---

## EventBridge

| 리소스 | 이름 | Schedule | Target | 비고 |
|--------|------|----------|--------|------|
| Reconcile rule | academy-reconcile-video-jobs | rate(15 minutes) | academy-video-ops-queue | JobDef: academy-video-ops-reconcile |
| Scan stuck rule | academy-video-scan-stuck-rate | rate(5 minutes) | academy-video-ops-queue | JobDef: academy-video-ops-scanstuck |

---

## IAM (Batch)

| 역할 | 이름 | 용도 |
|------|------|------|
| Batch 서비스 역할 | (AWS 관리형 또는 계정별 이름) | Batch가 CE/Queue 관리 |
| ECS 인스턴스 역할 | academy-batch-ecs-instance-role 등 | EC2 인스턴스 프로파일 |
| ECS 실행 역할 | academy-batch-ecs-task-execution-role 등 | 이미지 pull, 로그 |
| Job 역할 | academy-video-batch-job-role 등 | Job 내부에서 AWS/API 호출 시 |

정확한 역할 이름: `scripts/infra/batch_video_setup.ps1`, `scripts/infra/iam/*.json` 참조. 인벤토리 갱신 시 해당 스크립트 출력과 일치시키기.

---

## SSM

| Parameter | 타입 | 용도 |
|-----------|------|------|
| /academy/workers/env | SecureString | Batch Job 환경 변수(JSON). .env → ssm_bootstrap_video_worker.ps1로만 생성·갱신. |

스키마: [docs/deploy/SSM_JSON_SCHEMA.md](deploy/SSM_JSON_SCHEMA.md).

---

## ECR

| Repository | 이미지 태그 정책 |
|------------|------------------|
| academy-video-worker | immutable tag 필수. `:latest` 금지. |

---

## Messaging Worker (ASG)

| 리소스 | 이름/식별 | 비고 |
|--------|------------|------|
| ASG | academy-messaging-worker-asg | deploy_preflight.ps1에서 참조 |
| Launch Template | (ASG에 연결된 버전) | 변경 시 새 버전 생성 후 ASG만 업데이트 |

---

## R2 / CDN

| 항목 | 관리 주체 | 비고 |
|------|-----------|------|
| R2 버킷·엔드포인트 | 외부(Cloudflare) | 설정만 .env/SSM에 R2_* |
| CDN | 외부 | REFERENCE.md 등에서 언급 |

리소스 이름/ARN은 이 레포 SSOT에 없음. 환경별 값은 .env 및 SSM 스키마 참조.

---

## 태그 규칙 (리소스 유일 식별)

- Batch 리소스: 이름으로 유일. 태그는 선택(비용 할당 등).
- EC2: Tag Name으로 academy-api, academy-build-arm64, academy-messaging-worker 등 구분.
- **멱등성:** (name + tag set)으로 유일 식별 가능하도록 유지. SSOT는 이름 기준으로 정의.

---

## 환경별 값 요약 (prod vs 기타)

| 리소스 | prod | staging/dev |
|--------|------|-------------|
| VpcId | vpc-0831a2484f9b114c2 | 확인 필요(actual_state 또는 변수) |
| API Elastic IP | 15.165.147.157 | 확인 필요 |
| 리소스 이름(Batch/EventBridge) | 위 표와 동일 | 동일 권장 |
| SSM /academy/workers/env | 값만 env별(DB_*, R2_* 등) | 동일 파라미터 이름, 값만 상이 |

확인 방법: `aws batch describe-compute-environments --compute-environments academy-video-batch-ce-final --region ap-northeast-2`, `docs/deploy/actual_state/*.json` 참조.
