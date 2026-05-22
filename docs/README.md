# backend/docs

Backend 문서의 단일 진입점. 현재 동작 정본, 운영 절차, 리팩토링 계획, 과거 기록을 분리한다.

제품 전체 목표 아키텍처는 워크스페이스 루트 `ARCHITECTURE.md`에 둔다. 현재 실측과 실행 계획은 이 저장소의 [refactor/](refactor/)에서 관리한다.

## 진실 우선순위

충돌 시 아래 순서로 판단한다.

1. 실행 코드: `apps/`, `academy/`, `scripts/v1/`, `.github/workflows/`
2. 실행 SSOT: [ssot/](ssot/)
3. 현재 정책 문서: [architecture/](architecture/), [domain/](domain/), [infrastructure/](infrastructure/), [operations/](operations/)
4. 진행/예정 설계: [refactor/](refactor/)
5. 과거 기록: [releases/](releases/), [reports/](reports/)

## 폴더 트리

```text
backend/docs/
  README.md
  ssot/             # 코드/스크립트/CI가 직접 의존하는 정본
  architecture/     # 레이어, 모듈 경계, ADR, 큰 설계 결정
  domain/           # 현재 도메인 규칙과 상태머신
  infrastructure/   # AWS/Cloudflare/RDS/SQS/R2 구조와 예산
  operations/       # 배포, 장애대응, 운영 절차, 테넌트 셋업
    runbooks/       # 절차형 운영 runbook
    tenants/        # 테넌트별/도메인별 셋업
  refactor/         # 예정 리팩토링, 백로그, migration plan
  reports/          # 자동/수동 검증 보고서와 사고 기록
    history/        # audit/drift 스냅샷
    incidents/      # 사고 보고서
  releases/         # 봉인 릴리즈 기록, append-only
```

## 폴더 의미

| 폴더 | 의미 | 변경 방식 |
|------|------|-----------|
| [ssot/](ssot/) | 코드/스크립트/CI가 경로 그대로 읽는 정본 | 의존 코드와 동시 변경 |
| [architecture/](architecture/) | 레이어 책임, 배치 규칙, ADR | 큰 결정 시 갱신 |
| [domain/](domain/) | 도메인별 현재 정책·상태·불변 규칙 | 기능/정책 변경 시 갱신 |
| [infrastructure/](infrastructure/) | 인프라 구조, 용량, 비용, 자원 경계 | 인프라 변경 시 갱신 |
| [operations/](operations/) | 배포/운영/장애/테넌트 절차 | 실제 운영 절차 변경 시 갱신 |
| [refactor/](refactor/) | 예정 리팩토링과 백로그 | 완료 후 정본 문서로 흡수 |
| [reports/](reports/) | 자동 보고서, 감사, 사고 기록 | 자동 생성 또는 append-only |
| [releases/](releases/) | 봉인 릴리즈 기록 | append-only |

## 핵심 단축 경로

| 용도 | 경로 |
|------|------|
| 현재 봉인 릴리즈 | [releases/v1.2.1.md](releases/v1.2.1.md) |
| 실행 파라미터 | [ssot/params.yaml](ssot/params.yaml) |
| 레이어/코드 배치 | [architecture/hexagonal-cutover-policy.md](architecture/hexagonal-cutover-policy.md) |
| 배포 아키텍처 | [infrastructure/deployment-architecture.md](infrastructure/deployment-architecture.md) |
| 배포 경로 비교 | [operations/deployment-modes.md](operations/deployment-modes.md) |
| 수동 정식 배포 | [operations/formal-deploy.md](operations/formal-deploy.md) |
| 운영 runbook | [operations/runbooks/](operations/runbooks/) |

## 작성 규칙

- 현재 규칙은 `domain/`, `architecture/`, `infrastructure/`, `operations/` 중 하나에 둔다.
- 예정/제안/백로그는 `refactor/`에 둔다. 구현 완료 후 현재 정본 문서로 흡수한다.
- 사고/감사/검증 기록은 `reports/`에 둔다. 현재 정책처럼 서술하지 않는다.
- 봉인 릴리즈는 `releases/`에 두고 append-only로 관리한다.
- 한 주제는 한 파일에 둔다. 같은 내용을 여러 문서에 복제하지 않는다.
- 파일명은 kebab-case를 기본으로 하며, 기존 한국어 운영 문서명은 유지할 수 있다.
