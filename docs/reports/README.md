# reports — 자동 생성 보고서

CI/스크립트 자동 생성 산출물. **수동 편집 금지** (다음 실행 시 덮어쓰기).

## latest

| 파일 | 생성 주체 | 갱신 |
|------|-----------|------|
| [ci-build.latest.md](ci-build.latest.md) | `.github/workflows/v1-build-and-push-latest.yml` | main push 시 |
| [audit.latest.md](audit.latest.md) | audit 스크립트 | 수동 |
| [drift.latest.md](drift.latest.md) | drift 스크립트 | 수동 |
| [runtime-images.latest.md](runtime-images.latest.md) | `scripts/v1/run-deploy-verification.ps1` | 배포 검증 시 |
| [structure-refactor-2026-04-13.md](structure-refactor-2026-04-13.md) | 구조 리팩터 보고서 (1회성) | 보존 |

## history

`history/` 에 시점별 audit/drift 스냅샷 보관.

## 출력 정책

- CI/script 가 쓰는 경로는 절대 변경 금지 (`.github/workflows/*.yml` 와 `scripts/v1/*.ps1` 가 hardcode)
- latest 파일은 항상 마지막 실행 결과만 포함
- history 누적은 자동 (스크립트 책임)
