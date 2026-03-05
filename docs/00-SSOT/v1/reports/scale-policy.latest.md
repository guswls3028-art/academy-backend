# V1 API ASG 스케일 정책 (비용 절감)

**SSOT:** docs/00-SSOT/v1/params.yaml  
**결정 시각:** 2026-03-06  
**상태:** 서비스 런칭 전, 비용 최우선.

## 결정

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
