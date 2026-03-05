# V1 리소스 정리 기록 (증거)

**리전:** ap-northeast-2 **SSOT:** docs/00-SSOT/v1/params.yaml  
**규칙:** 삭제는 SSOT에 없는 리소스만. 삭제 전 inventory + 본 문서에 근거 기록.

## Elastic IP

| AllocationId | PublicIp | Associated | 조치 | 시각 |
|--------------|----------|------------|------|------|
| (run cleanup-legacy.ps1 -Execute 후 위 표 갱신) | | | | |

- Orphan(AssociationId 없음) → release. NAT EIP는 제외.

## EBS 볼륨 (State=available)

| VolumeId | Size | 생성시각 | 조치 | 시각 |
|----------|------|----------|------|------|
| (cleanup-legacy.ps1 실행 결과로 갱신) | | | | |

## Security Group (ENI 0개, SSOT keep 아님)

| GroupId | GroupName | 조치 | 시각 |
|---------|-----------|------|------|
| (cleanup-legacy.ps1 실행 결과로 갱신) | | | |

## Legacy ASG (SSOT 3개 외)

| ASG Name | Desired | 조치 | 시각 |
|----------|---------|------|------|
| (inventory 출력 또는 삭제 결과로 갱신) | | | |

- 유지: academy-v1-api-asg, academy-v1-messaging-worker-asg, academy-v1-ai-worker-asg (및 Batch 관리 ASG).
