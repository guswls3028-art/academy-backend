# operations

배포, 운영, 장애 대응, 테넌트 셋업 절차의 정본.

## 배포

| 문서 | 스코프 | 사용 시점 |
|------|--------|-----------|
| [deployment-modes.md](deployment-modes.md) | CI 자동 배포 vs 수동 정식 배포 | 배포 경로 선택 |
| [formal-deploy.md](formal-deploy.md) | `deploy.ps1` 동작 상세 | 인프라 반영/정식 배포 |
| [배포.md](배포.md) | legacy 인프라 부트스트랩 노트 | 새 환경/리전 셋업 전 `deployment-modes.md`와 실행 스크립트 재확인 |
| [ssm-json-schema.md](ssm-json-schema.md) | SSM `/academy/api/env` JSON 스키마 | env 키 추가/변경 |

## 평시 운영

| 문서 | 스코프 |
|------|--------|
| [운영.md](운영.md) | legacy 혼합 운영 노트. 영상/배포 절차는 runbook 우선 | 일상 운영 참고 |
| [operations-baseline.md](operations-baseline.md) | 배포/CI/보안/observability baseline |
| [local-dev-db.md](local-dev-db.md) | 로컬 개발 DB 셋업 |
| [billing-go-live-checklist.md](billing-go-live-checklist.md) | Toss 자동결제 오픈 전 체크리스트 |

## Runbooks

| 문서 | 스코프 |
|------|--------|
| [runbooks/](runbooks/) | runbook 인덱스 |
| [runbooks/deploy-checklist.md](runbooks/deploy-checklist.md) | 배포 전 체크리스트 |
| [runbooks/disaster-recovery.md](runbooks/disaster-recovery.md) | DB 장애/복구 |
| [runbooks/emergency-mode.md](runbooks/emergency-mode.md) | 긴급 모드 |
| [runbooks/incidents.md](runbooks/incidents.md) | 사고 일반 대응 |
| [runbooks/matchup-segmentation-qa.md](runbooks/matchup-segmentation-qa.md) | 매치업 문항분리 감사/회귀 게이트 |
| [runbooks/ops-prohibited.md](runbooks/ops-prohibited.md) | 운영 금지 사항 |
| [runbooks/problem-studio-source-transfer-uat.md](runbooks/problem-studio-source-transfer-uat.md) | 문제 제작 원본 이관 실사용 검수 |
| [runbooks/video-batch.md](runbooks/video-batch.md) | 영상 Batch 운영 |

## 테넌트 셋업

| 문서 | 스코프 |
|------|--------|
| [tenants/sswe-checklist.md](tenants/sswe-checklist.md) | SSWE 테넌트 셋업 체크리스트 |
| [tenants/custom-domain.md](tenants/custom-domain.md) | 신규 테넌트 커스텀 도메인 등록 |
| [tenants/gabia-nameserver.md](tenants/gabia-nameserver.md) | 가비아 네임서버 셋업 |

## 작성 규칙

- 실제 운영 절차만 둔다.
- 인프라 구조/용량 설명은 `../infrastructure/`에 둔다.
- 사고 기록은 `../reports/incidents/`에 둔다.
- 예정 작업과 리팩토링 계획은 `../refactor/`에 둔다.
