# SSOT Path Alias Policy

## 목적

`docs/00-SSOT/v1/...` alias와 실제 운영 경로가 혼재되어 발생하는 오작동을 방지한다.

## Authoritative paths

- 실행 SSOT: `docs/00-SSOT/params.yaml`
- 배포/CI 문서 SSOT: `docs/00-SSOT/v1.1.0/`
- SSOT 인덱스/정책: `docs/00-SSOT/README.md`
- 실행 스크립트: `scripts/v1/`

## v1 alias 정책

- `docs/00-SSOT/v1/...`는 **deprecated alias**로 취급한다.
- 신규 코드/문서/룰/스크립트에서 alias를 추가하지 않는다.
- 실행 경로에서 alias가 발견되면 우선 authoritative path로 교체한다.
- 과거 문서(`archive/*`, 과거 보고서)는 이력 보존을 위해 즉시 일괄 수정하지 않는다.

## 경로 충돌 시 우선순위

1. `scripts/v1` 실행 코드
2. CI workflow(`.github/workflows`)
3. `docs/00-SSOT/` 문서

## 마이그레이션 원칙

- 1단계: README/룰/실행 스크립트의 active 경로 정규화
- 2단계: 운영 문서(`docs/02-OPERATIONS`)의 alias 점진 정리
- archive 문서는 참고용이므로 대규모 치환 금지
