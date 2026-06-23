# operations/runbooks

절차형 운영 runbook 모음. 장애나 배포 중 바로 실행할 수 있게 쓴다.

| 문서 | 사용 시점 |
|------|-----------|
| [deploy-checklist.md](deploy-checklist.md) | 배포 전 점검 |
| [disaster-recovery.md](disaster-recovery.md) | DB 장애/복구 |
| [emergency-mode.md](emergency-mode.md) | 긴급 모드 |
| [incidents.md](incidents.md) | 사고 일반 대응 |
| [matchup-segmentation-qa.md](matchup-segmentation-qa.md) | 매치업 문항분리 감사/회귀 게이트 |
| [ops-prohibited.md](ops-prohibited.md) | 운영 금지 사항 |
| [problem-studio-source-transfer-uat.md](problem-studio-source-transfer-uat.md) | 문제 제작 원본 이관 실사용 검수 |
| [video-batch.md](video-batch.md) | 영상 Batch 운영 |

## 작성 규칙

- 실행 순서, 사전 조건, 검증 방법을 포함한다.
- 의도나 배경 설명은 짧게 유지하고, 상세 설계는 `../../architecture/` 또는 `../../infrastructure/`로 연결한다.
- 실제 실행 경로가 바뀌면 즉시 갱신한다.
