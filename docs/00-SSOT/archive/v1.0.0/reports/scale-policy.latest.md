# V1 API ASG 스케일 정책 (비용 절감)

**SSOT:** docs/00-SSOT/v1/params.yaml  
**결정 시각:** 2026-03-06  
**상태:** 서비스 런칭 전, 비용 최우선.

## API ASG 결정

| 항목 | 이전 | 변경 후 | 이유 |
|------|------|---------|------|
| api.asgMinSize | 2 | **1** | 런칭 전 접속 테스트만 필요, 1대로 충분 |
| api.asgDesiredCapacity | 2 | **1** | 동일 |
| api.asgMaxSize | 4 | **2** | 비용 절감. 필요 시 3까지 상향 가능 |

## 적용 방법

- SSOT(params.yaml)만 수정. 배포 시 `scripts/v1/deploy.ps1` 이 Ensure-API-ASG에서 capacity drift 시 `update-auto-scaling-group` 로 min/desired/max 반영.
- Instance refresh 가 이미 InProgress 이면 완료 대기 후 진행(api.ps1 유지).
- 완료 조건: API ASG desired=1, EC2 Name=academy-v1-api running 1대, ALB target healthy 1/1, /health 200.

## 런칭 후 권장

- 트래픽 증가 시 min/desired=2, max=4 등으로 params.yaml 조정 후 재배포.

---

## AI 워커 (엑셀 학생등록 상시 대기)

| 항목 | 값 | 설명 |
|------|-----|------|
| aiWorker.minSize | 1 | 상시 1대 대기. **min=1 이므로 scale-in 정책이 발동해도 desired가 1 밑으로 내려가지 않음.** |
| aiWorker.desiredCapacity | 1 | 동일 |
| aiWorker.maxSize | 5 | 런칭 전 2~5 권장. 비용/안정성 균형. |
| aiWorker.scaleInProtection | true | 신규 인스턴스 scale-in 보호. “스케일이 안 줄어드는 문제” 발생 시 long-running 작업만 보호하도록 완화 검토. |
| aiWorker.visibilityTimeoutSeconds | 1800 | 30분. 엑셀 처리 worst-case 기준. graceful shutdown 시 in-flight 유실 방지. |
| scaleInThreshold | 0 | 큐 메시지 ≤0 이면 scale-in 알람. ASG min=1 이므로 실제 desired 최소 1 유지. |

### 애플리케이션 요구사항 (SSOT 외)

- **엑셀 등록 작업:** 멱등키(idempotency key)로 중복 실행 방지. DLQ로 빠진 메시지도 재처리 시 멱등 처리.
- **Graceful shutdown:** 워커 종료 시 in-flight 메시지 처리 완료 또는 visibility 복귀 후 종료.

### SQS 반영

- Bootstrap(Ensure-SQS)에서 aiWorker.visibilityTimeoutSeconds 를 SSOT에서 읽어 AI 큐 VisibilityTimeout 에 반영. (ssot.ps1 AiVisibilityTimeoutSeconds, bootstrap.ps1 사용)
