# backend/docs — 진입점

프로젝트 모든 문서의 시작점. 어디 가야 할지 알 수 있게.

## 진실 우선순위 (충돌 시)
1. `scripts/v1/` 실행 코드
2. `.github/workflows/` CI 워크플로우
3. `docs/ssot/params.yaml` 실행 파라미터
4. SSOT 문서 (아래)

## 폴더 의미

| 폴더 | 의미 | 변경 빈도 | 진입 |
|------|------|-----------|------|
| **[ssot/](ssot/)** | 코드/CI 가 직접 의존하는 SSOT (params/ID/path-alias/messaging-policy) | 낮음 (변경 = 인프라/스크립트 동시 수정) | [ssot/README.md](ssot/README.md) |
| **[domain/](domain/)** | 도메인 SSOT (헥사고날/메시징/OMR/매치업/커뮤니티/평가 etc) | 중간 (도메인 정책 진화) | [domain/README.md](domain/README.md) |
| **[infrastructure/](infrastructure/)** | 인프라/배포 SSOT (deployment-architecture, runbooks) | 낮음 (RDS Proxy 같은 큰 변경 시) | [infrastructure/README.md](infrastructure/README.md) |
| **[architecture/](architecture/)** | 설계 결정 + ADR | 낮음 (큰 결정 시) | [architecture/README.md](architecture/README.md) |
| **[operations/](operations/)** | 운영 실무 가이드 (배포/일상/테넌트) | 중간 | [operations/README.md](operations/README.md) |
| **[releases/](releases/)** | 버전별 RELEASE-NOTES (v1.0.3~v1.2.0) | append-only | [releases/README.md](releases/README.md) |
| **[reports/](reports/)** | CI/스크립트 자동 생성 보고서 | 자동 | [reports/README.md](reports/README.md) |

## 핵심 단축 경로

| 용도 | 경로 |
|------|------|
| **현재 버전 RELEASE-NOTES** | [releases/v1.2.0.md](releases/v1.2.0.md) — 매치업 신규 도메인 + RDS Proxy + 헥사고날 컷오버 (봉인 2026-04-30) |
| 실행 파라미터 SSOT | [ssot/params.yaml](ssot/params.yaml) — 스크립트가 직접 로드 |
| 헥사고날 컷오버 정책 | [domain/hexagonal-cutover-policy.md](domain/hexagonal-cutover-policy.md) — `academy/` vs `apps/` 경계 |
| 배포 아키텍처 | [infrastructure/deployment-architecture.md](infrastructure/deployment-architecture.md) |
| 배포 경로 비교 | [operations/deployment-modes.md](operations/deployment-modes.md) |
| 배포 스크립트 | [../scripts/v1/deploy.ps1](../scripts/v1/deploy.ps1) |

## 작성 규칙

- 새 문서 → 위 7개 폴더 중 하나 + 해당 폴더 README 표에 추가
- 한 주제 = 한 파일. 같은 주제 분산 금지
- 파일명 = kebab-case (한국어 파일명은 한국어 그대로)
- 이전 버전 SSOT 보존 = `releases/archive/` 사용
- 일회성 조사/검증 보고서 = `_artifacts/sessions/reports/` 또는 PR 마무리 후 삭제
