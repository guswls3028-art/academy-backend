# operations — 운영 실무 가이드

운영·배포·테넌트 셋업 실무 문서. 각 문서 스코프 1줄 요약.

## 배포 / 인프라

| 문서 | 스코프 | 사용 시점 |
|------|--------|-----------|
| [배포.md](배포.md) | 인프라 부트스트랩 (RDS/SQS/EC2/IAM 처음부터 끝까지) | 새 환경/리전 셋업 |
| [deployment-modes.md](deployment-modes.md) | 배포 경로 비교 (CI 자동 vs deploy.ps1) | 어느 경로 쓸지 결정 |
| [formal-deploy.md](formal-deploy.md) | `deploy.ps1` 동작 상세 | 수동 정식 배포 실행 |
| [ssm-json-schema.md](ssm-json-schema.md) | SSM `/academy/api/env` JSON 스키마 | env 키 추가/변경 |

## 일상 운영

| 문서 | 스코프 | 사용 시점 |
|------|--------|-----------|
| [운영.md](운영.md) | 일상 운영 체크/엑셀 흐름/학생 복구/관리 명령 | 평시 운영 |
| [local-dev-db.md](local-dev-db.md) | 로컬 개발 DB 셋업 | 로컬 환경 구성 |
| [video-batch-runbook.md](video-batch-runbook.md) | 영상 Batch 운영 (트랜스코드) | 영상 인코딩 이슈 |
| [disaster-recovery-runbook.md](disaster-recovery-runbook.md) | 장애 복구 절차 | 장애 발생 시 |

## 신규 테넌트 셋업

| 문서 | 스코프 |
|------|--------|
| [tenants/sswe-checklist.md](tenants/sswe-checklist.md) | SSWE 테넌트 셋업 체크리스트 |
| [tenants/custom-domain.md](tenants/custom-domain.md) | 신규 테넌트 커스텀 도메인 등록 |
| [tenants/gabia-nameserver.md](tenants/gabia-nameserver.md) | 가비아 네임서버 셋업 |

## 작성 규칙

- 새 운영 문서 → 위 3그룹(배포/일상/테넌트) 중 하나 + README 표에 추가
- 한 주제 = 한 파일. 같은 주제 분산 금지
- 파일명 = kebab-case (한국어 파일명은 한국어 그대로)
