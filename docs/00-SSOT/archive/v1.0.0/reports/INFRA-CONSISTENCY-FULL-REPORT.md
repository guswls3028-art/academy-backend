# 인프라 전체 정합성·작동테스트·리소스 정리 보고서

**Generated:** 2026-03-07
**리전:** ap-northeast-2
**SSOT:** docs/00-SSOT/v1/params.yaml

---

## 1. 정합성 체크 결과

### 1.1 SSOT ↔ Actual

| 항목 | SSOT | Actual | 결과 |
|------|------|--------|------|
| API ASG min/desired | 1/1 | 1/1 | PASS |
| AI ASG min/desired | 1/1 | 1/1 | PASS |
| Messaging ASG min/desired | 1/1 | 1/1 | PASS |
| Messaging SQS VisibilityTimeout | 900 | 900 | Yes |
| AI SQS VisibilityTimeout | 1800 | 1800 | Yes |
| RDS | academy-db | available | PASS |
| Redis | academy-v1-redis | available | PASS |
| Video Batch CE/Queue | VALID/ENABLED | VALID/ENABLED | PASS |
| Video Ops CE/Queue | VALID/ENABLED | VALID/ENABLED | PASS |
| EventBridge reconcile/scan-stuck | ENABLED | ENABLED | PASS |

### 1.2 Drift

| ResourceType | Name | Action |
|--------------|------|--------|
| API LT | academy-v1-api-lt | NewVersion (UserData/AMI drift) |

### 1.3 합의사항

| 항목 | 결과 |
|------|------|
| Solapi 고정 IP(NAT/EIP) 취소 | WARNING (EIP 3개 연결됨 — NAT/ALB 등 사용 중) |
| 빌드 서버 0대 | PASS |

---

## 2. 작동 테스트

| 항목 | 결과 | 비고 |
|------|------|------|
| API /healthz | 200 OK | ALB DNS 직접 호출 |
| ALB target health | 1/2 healthy | instance-refresh 진행 중일 수 있음 |
| RDS | available | |
| Redis | available | |
| SQS Messaging DLQ | 0 | PASS |
| SQS AI DLQ | 0 | PASS |
| Messaging queue consume | 대기 중 | instance-refresh 완료 후 신규 워커(IamProfile+UserData) 소비 예상 |
| Video Batch | VALID | JobDef rev 20 |

---

## 3. 리소스 인벤토리 (요약)

### EC2 인스턴스 (KEEP)
- academy-v1-api, academy-v1-messaging-worker, academy-v1-ai-worker
- Batch CE 관리 인스턴스 (video-batch, video-ops)

### ASG (KEEP)
- academy-v1-api-asg, academy-v1-messaging-worker-asg, academy-v1-ai-worker-asg
- academy-v1-video-batch-ce-asg-*, academy-v1-video-ops-ce-asg-*

### EIP
- 3개 모두 연결됨 (미연결 0) — release 대상 없음

### Security Groups
- academy-v1-sg-app, academy-v1-sg-batch, academy-v1-sg-data, default: KEEP
- academy-rds, academy-redis-sg: 사용 중 (RDS/Redis)
- academy-api-sg, academy-worker-sg: 이미 삭제됨 (InvalidGroup.NotFound)

---

## 4. 불필요 리소스 정리

### 4.1 수행 내용
- **cleanup-unused-ec2.ps1** DryRun: EIP 미사용 0, Orphan 인스턴스 0
- **cleanup-orphans.ps1** Execute: Orphan SG 3건 시도 — 이미 삭제됨 (stale ID)
- **cleanup-unused-ec2.ps1** 수정: Batch video-batch-ce ASG 패턴 Keep 목록 추가

### 4.2 cleanup-orphans.ps1 개선
- 하드코딩 SG ID 제거 → 이름 기반 동적 탐색 (academy-api-sg, academy-worker-sg, academy-v1-vpce-sg 0 ENI만)

### 4.3 resource-cleanup-plan.latest.md
- 삭제 대상: 없음 (현재 SSOT와 정합)

---

## 5. 후속 확인

1. **API LT drift**: deploy.ps1 실행 시 API LT 새 버전 적용·instance-refresh
2. **Messaging/AI 워커 SQS 소비**: instance-refresh 완료 후 academy-v1-messaging-queue 메시지 소비 여부 확인
3. **EIP**: NAT 미사용(natEnabled: false)인데 EIP 3개 연결 — NAT Gateway 또는 기타 사용 여부 확인

---

## 6. 관련 스크립트

| 스크립트 | 용도 |
|----------|------|
| run-deploy-verification.ps1 | 정합성·Evidence·보고서 갱신 |
| run-resource-inventory.ps1 | 리소스 인벤토리·정리 계획 생성 |
| cleanup-unused-ec2.ps1 | EIP/Orphan EC2 정리 (-Execute) |
| cleanup-orphans.ps1 | Orphan ENI/SG/EventBridge (-Execute -DryRun:$false) |
