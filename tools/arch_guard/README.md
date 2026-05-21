# arch_guard — 백엔드 아키텍처 경계 게이트

> ⚠️ 이 문서는 참고용. 동작의 진실은 `check_boundaries.py` 코드와 실제 실행 결과다.

`ARCHITECTURE.md`(워크스페이스 루트) / `backend/docs/domain/hexagonal-cutover-policy.md` 의 경계
규칙을 **사람의 기억이 아니라 빌드가 강제**하게 만드는 의존성 0 정적 체커. Refactor 로드맵
(`backend/docs/refactor/roadmap.md`) **Phase 0-B** 산출물.

## 무엇을 검사하나

| rule | 위반 | 목표 경로 |
|------|------|----------|
| `cross_domain` | `apps/domains/<A>` 가 다른 도메인 `<B>` 의 내부 모듈 직접 import | `apps.domains.<B>.contracts` 경유 (유일 허용 표면) |
| `infra_in_domain` | `apps/domains/*` 가 boto3 / redis / requests / cv2 / fitz(PyMuPDF) / libs.r2_client / apps.infrastructure.storage / google.cloud.vision 직접 import | `academy(kernel)/adapters` 경유 |

테스트(`tests/`, `test_*.py`, `tests.py`, `conftest.py`) · 마이그레이션 · `__pycache__` 는 제외.
상대 import(`from ..matchup.models`)도 절대 모듈로 해석해 검사한다.

## baseline 방식 (inline-style / Badge 게이트와 동일 패턴)

전수 청소는 비현실적이므로 **현재 위반을 동결(freeze)하고 신규만 차단**한다.

1. **freeze** — `python tools/arch_guard/check_boundaries.py --update-baseline` → 현재 위반 전부를 `baseline.json` 에 기록.
2. **block new** — 평시/CI 실행은 baseline 에 *없는* 위반만 실패(exit 1). 기존 debt 는 통과.
3. **burn-down** — 코드를 고쳐 위반이 사라지면 `stale` 로 보고됨. `--update-baseline` 로 갱신해 ledger 를 줄인다(touch-the-file).
4. **promote** — 한 도메인이 0 debt 에 도달하면 그 도메인을 strict 화이트리스트로 올려 영구 잠금(향후 확장).

baseline key 는 `rule|relpath|target` (line 번호 제외) 이라 파일 편집에 깨지지 않는다.

현재 동결 규모 (작성 시점, **반드시 재실측**): cross_domain 523 / infra_in_domain 92 (key 중복 제거 496).

## 사용법

```bash
# baseline 대비 검사 (CI 기본)
python tools/arch_guard/check_boundaries.py

# 현재 위반을 baseline 으로 동결/갱신
python tools/arch_guard/check_boundaries.py --update-baseline

# 기계 판독 JSON (current/baseline/new/stale)
python tools/arch_guard/check_boundaries.py --json

# self-test: 임의 루트로 검사
python tools/arch_guard/check_boundaries.py --root <dir>
```

exit code: `0` = 신규 위반 없음 / `1` = 신규 위반 있음 / `2` = 설정 오류.

## 왜 import-linter 가 아니라 ast 체커인가 (Phase 0 결정)

import-linter / grimp 는 전체 패키지 *import 그래프*를 만들기 위해 모듈을 실제 import 해야 하고,
이는 `DJANGO_SETTINGS_MODULE` + 모든 런타임 의존성 설치 + (간접적으로) DB/env 부트스트랩을
요구한다 — 로컬·CI 양쪽에서 취약하고 무겁다. arch_guard 는 **stdlib `ast` 정적 분석만** 쓰므로
의존성 0, Django 부트스트랩 0, 어디서나 즉시 실행/검증 가능하다.

→ 더 깊은 **레이어링 contract**(`kernel.domain < application < adapters`, `adapters -/-> application`
역방향 금지 등 ARCHITECTURE §2.3 의 나머지)는 ast 로 표현이 약하므로, 그 단계에서 import-linter 를
*추가로* 얹는다. arch_guard 는 가장 빈발하고 운영 사고로 직결되는 두 규칙(cross-domain / infra)을
지금 즉시 잠근다.

## CI

`.github/workflows/arch-guard.yml` 가 PR/푸시에서 실행. 의존성 설치 불필요(stdlib only).
배포 파이프라인(`v1-build-and-push-latest.yml`)과 **독립된 게이트**라 배포에 영향 없음.
