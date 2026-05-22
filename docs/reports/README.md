# reports

검증 보고서, 감사 결과, 사고 기록. 현재 정책처럼 서술하지 않는다.

## latest

| 파일 | 생성 주체 | 갱신 |
|------|-----------|------|
| [ci-build.latest.md](ci-build.latest.md) | `.github/workflows/v1-build-and-push-latest.yml` | main push 시 |
| [audit.latest.md](audit.latest.md) | audit 스크립트 | 수동 |
| [drift.latest.md](drift.latest.md) | drift 스크립트 | 수동 |
| [runtime-images.latest.md](runtime-images.latest.md) | `scripts/v1/run-deploy-verification.ps1` | 배포 검증 시 |

## history

[history/](history/)에는 audit/drift 스냅샷을 보관한다.

## incidents

| 파일 | 사건 |
|------|------|
| [incidents/incident-2026-03-23-db-auth-failure.md](incidents/incident-2026-03-23-db-auth-failure.md) | DB 인증 실패/connection exhaustion |

## one-off reports

| 파일 | 용도 |
|------|------|
| [structure-refactor-2026-04-13.md](structure-refactor-2026-04-13.md) | 구조 리팩터 보고서 |

## 출력 정책

- CI/script가 쓰는 `*.latest.md` 경로는 변경하지 않는다.
- 최신 보고서는 마지막 실행 결과만 포함한다.
- 과거 스냅샷과 사고 기록은 append-only로 보존한다.
