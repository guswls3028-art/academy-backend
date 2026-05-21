# Refactor Roadmap — 현재 → 이상적 구조 이주 계획

**Status:** Active plan · 작성 2026-05-21
**목적지 정본:** `C:\academy\ARCHITECTURE.md` (이 문서는 거기로 가는 *경로*).
**보정 입력 (사용자 directive 2026-05-21):**
- 직원 합류 1년+ → **팀 준비 레이어는 가볍게 병행, 딥 리팩터 우선.**
- **Phase 1(FE SSOT) · Phase 2(tenant 안전) 둘 병행.**
- **SEALED 전부 포함** — results→exam_results rename, homework 통합, 매치업 구조, ClinicSubmission, academy→kernel 모두 범위 안.
- 문서: 루트 `ARCHITECTURE.md` + 본 파일.

---

> ## ⚠️ 이 문서는 참고용. 유일한 진실은 실측한 코드다.
>
> 아래 단계별 수치(파일명·줄수·결합 카운트·"현재 N곳" 묘사·도메인 목록)는 **작성 시점 스냅샷**이며 작업 진행에 따라 빠르게 낡는다. 잘못 측정했을 수도 있다.
>
> **각 Phase 착수 시 반드시 `Grep`/`Read`로 현황을 재실측**하고, 본 문서 수치를 그대로 받아 작업하지 말 것. 코드와 문서가 다르면 코드가 진실이다(core.md §3). 발견 즉시 본 문서를 코드에 맞춰 갱신한다. 단계 *순서·방향성*은 directive로 유지하되, *현황·범위*는 매번 코드로 검증.

---
## 0. 원칙 — 빅뱅 금지, Strangler + Baseline

330K줄 라이브 멀티테넌트 SaaS는 **빅뱅 재작성 불가**(데이터 무결성 최상위). 목적지는 전부 설계하되, 도달은:

1. **Strangler**: 새 구조를 옆에 세우고 호출을 점진 이전. 매 PR이 독립 배포 가능하고 운영 무중단.
2. **Baseline 동결**: 새 경계 게이트는 현재 위반을 화이트리스트로 동결 → 신규만 CI 차단 → touch-the-file 소각 → 0 도달 시 warn→error 승격. (inline-style·Badge에서 이미 검증된 패턴.)
3. **안전망 선행**: 테스트 없는 영역은 손대기 전에 characterization test부터.
4. **rename = 데이터 마이그레이션 0**: 테이블명 유지, Python 경로·클래스명만 이동. immutable 정책(domain-policy §1~9) 100% 보존.

각 단계 산출물 = 동작 변화 0 + 경계/SSOT 1개 확보 + 그걸 잠그는 게이트 1개.

---

## Phase 0 — 안전망 & 강제 스캐폴딩 (refactor 전 필수)

**목표: 출혈 정지. 청소 전에 "신규 위반 차단"부터.** 이 단계 끝나면 코드베이스는 *더 나빠질 수 없다*.

| 작업 | 내용 | 검증 |
|---|---|---|
| 0-A characterization test | 무테스트 5도메인(parents·schedule·teachers·teacher_app·tools) + 분해 예정 god object(matchup/services 2,010, views_hit_report 1,770, student_app/media/views 1,456)에 현재 동작 박제 테스트 | 테스트가 현재 동작에 green |
| 0-B import-linter 도입(BE) | ARCHITECTURE §2.3 contract 5종 작성. **현재 353+ cross-domain·30 R2·6 boto3·9 역방향 위반을 baseline 동결.** 신규만 CI 차단 | CI에 위반 카운터 아티팩트 출력 |
| 0-C eslint-plugin-boundaries + dependency-cruiser(FE) | app/domain import 경계 정의, baseline 동결 | 신규 경계 위반 CI warn |
| 0-D drift 가시화 | 위반 수를 CI 출력(inline-style "4-15 1,790→4-29 5,442" 추적 방식) | 매 PR 추세 노출 |

**Drift-lock:** CI가 신규 경계 위반 거부. **소요 감각: 2~3주.** 코드 이동 0, 순수 안전장치.

---

## Phase 1 + Phase 2 (병행) — FE SSOT 통일 & BE tenant 안전·adapter 격리

> 사용자 directive: 둘 병행. FE는 체감 효과(미반영 제거), BE는 최고 위험 차단. 서로 다른 파일군이라 충돌 적음.

### Phase 1 — 프론트 SSOT 통일 (체감 통증 직격)

| 작업 | 내용 |
|---|---|
| 1-A 스키마 다리 | `drf-spectacular` 도입 → `openapi.json` → `openapi-typescript` → `shared/api/generated/types.ts`. CI codegen + drift 게이트 |
| 1-B 타입 단일화 | 손 복제 엔티티 타입(Student·Lecture·Exam·ExamAsset 등 15+) → 생성 타입으로 교체, 중복 정의 삭제 |
| 1-C format SSOT | `shared/format/` 신설 → `formatDate` 15곳 흡수. eslint로 로컬 재정의 차단 |
| 1-D status SSOT | `shared/status/` 신설 → clinic·enrollment·submission·video 라벨/색 매핑 1곳. 인라인 map 흡수 (StudentsDetailOverlay 776·855행 등) |
| 1-E error/util SSOT | `apiErrorMessage` 로컬 재구현 3~4곳 → 기존 shared로 통일 |
| 1-F query 규약 | react-query key factory + invalidation 규약 → stale 화면(미반영) 버그 제거 |

**검증:** BE serializer 임의 필드 변경 → FE tsc 에러로 즉시 드러나는지 E2E. 효과가 눈에 보임.
**Drift-lock:** 타입은 생성물(손수정 불가), format/status 로컬 재정의 eslint 차단.

### Phase 2 — BE tenant 안전 & adapter 격리 (최고 위험)

| 작업 | 내용 |
|---|---|
| 2-A repository 도입 | context별 `repositories.py` — 모든 쿼리가 tenant 스코프 강제 통과 |
| 2-B tenant 수렴 | 384곳 raw `.filter(tenant=)` → repository/`TenantQuerySet` 경유 점진 이전 (현재 56파일만 사용) |
| 2-C adapter 격리 | R2 30파일·boto3 6파일·requests를 `kernel/adapters/` 경유로 이관 (cutover-policy에 이미 명시) |
| 2-D 역방향 해소 | `kernel.adapters→application` 역 import 9곳(schema_normalizer·repositories_matchup_proposal 등) DTO를 `kernel/domain/shared`로 분리해 해소 |

**검증:** import-linter contract green + tenant 격리 회귀 E2E(다른 테넌트 데이터 미노출).
**Drift-lock:** 신규 raw tenant filter·인프라 직호출 CI 거부.

**병행 소요 감각: 6~10주.**

---

## Phase 3 — Bounded Context 재편 & 결합 해체 (구조의 핵심)

**목표: ARCHITECTURE §2.1~2.2 구조 물리 실현. 콜패스 추적 가능 → 디버깅 병목 제거.**

| 작업 | 내용 |
|---|---|
| 3-A contracts 도입 | 각 context에 `contracts.py` 신설. cross-domain 호출을 contract 함수로 전환 |
| 3-B 최악 결합 해체 | `results`(8도메인)·`clinic`(6)·`inventory→matchup`(6 call)·`attendance`(5)를 contracts 경유로 |
| 3-C view 다이어트 | 비대 view 비즈니스 로직 → services. `admin_exam_total_score_view`(patch 225줄)·`student_app/media/views`(1,456줄) 등 |
| 3-D grading 정본화 | grading 3중 서비스(`grading_service`/`exam_grading_service`/`student_result_service`) → 단일 정본 + 위임 |
| 3-E 폴더 이동 | `apps/`+`academy/` → `platform/`+`contexts/`+`bff/`+`kernel/`. git mv로 history 보존, `__init__` re-export로 호환 |

**검증:** Phase 0 characterization test 전부 green 유지 + import-linter 신 구조 contract green.
**Drift-lock:** context 간 직접 model import CI 거부 (contracts만).
**소요 감각: 10~16주.** 가장 큰 단계 — 다회 PR로 context 하나씩.

---

## Phase 4 — God Object 분해 (이제 안전)

테스트(Phase 0) + 경계(Phase 2·3)가 깔린 뒤에만:

- BE: `kernel/adapters/.../tier0_native_pdf.py`(2,751), `contexts/content/matchup/services.py`(2,010, 5책임 분리), `views_hit_report.py`(1,770 — ⚠️학원장 데이터, `_artifacts/backups/` 백업 후), `matchup_pipeline.py`(2,397)
- FE: `MatchupPage.tsx`(2,041), `ClinicConsoleWorkspace.tsx`(1,879), `ScoresTable.tsx`(1,520), `AnswerKeyRegisterModal.tsx`(1,288)

**검증:** 분해 전후 동작 동일(test) + 시각 E2E.
**소요 감각: 6~10주, 도메인별 점진.**

---

## Phase 5 — SEALED·잔여 부채 정리 (사용자: 전부 포함)

| 작업 | 내용 | 위험 |
|---|---|---|
| 5-A results rename | `domains/results` → `contexts/assessment/exam_results`. 클래스명·import 경로 이동, 테이블명 유지 | 회귀 위험 큼 → Phase 0 테스트 전제 |
| 5-B homework 통합 | homework + homework_results → `assessment/homework/`. URL `/homework/`·`/homeworks/` 이원화 통합. **FE api.ts 동반 수정** | 중 |
| 5-C 네이밍 충돌 | `clinic.Submission` → `ClinicSubmission` (state-transitions F8) | 저 |
| 5-D academy→kernel | 트리 rename (Phase 3-E와 묶거나 직후) | 저(기계적) |
| 5-E 잔여 | exam_status deprecated 정리(F7), R-11 inline-style baseline 소각, Submission EXTRACTING orphan(F1) 정리 | 저 |

**검증:** rename은 데이터 마이그레이션 0 확인 + 전 E2E + 운영 smoke. immutable 정책 보존 확인.
**소요 감각: 6~8주.**

---

## Phase 6 — 게이트 승격 & 팀 준비 (1년+이므로 가볍게, 마지막)

| 작업 | 내용 |
|---|---|
| 6-A 게이트 승격 | baseline이 0 도달한 contract부터 warn→error. 코드베이스 영구 잠금 |
| 6-B ARCHITECTURE 유지 | 실제 구조와 §2.4 결정 트리 동기화 |
| 6-C 신입 온보딩 | PR 템플릿 + 경계 체크리스트, CODEOWNERS(SEALED 영역 리뷰 강제), Tenant 1 E2E 가이드 문서화 |

**소요 감각: 상시 + 합류 직전 집중.**

---

## 리스크 레지스터

| 리스크 | 완화 |
|---|---|
| rename/이동 중 회귀 (특히 results·매치업) | Phase 0 characterization test 선행 필수. 테스트 없는 영역 손대기 금지 |
| 학원장 데이터 손상 (immutable 정책) | rename은 경로만, 데이터 마이그레이션 0. matchup/hit_report 작업 전 `_artifacts/backups/` |
| 대규모 PR 리뷰 부담 (1인) | context 하나·게이트 하나 단위 다회 PR. `git add -A` 금지(infra-policy §5) |
| 신규 기능 개발과 충돌 | 새 코드는 처음부터 목표 구조로 작성(결정 트리). 리팩터는 touch-the-file 병행 |
| codegen 도입이 기존 FE 깨뜨림 | 신규/변경 endpoint부터 점진. 기존 타입은 baseline |
| tenant repository 강제가 엣지 누락 | 격리 회귀 E2E + import-linter, 단계적 수렴 |

## 진행 추적

각 Phase 완료 시 이 표 갱신 (✅/🚧/⬜):

| Phase | 상태 | 비고 |
|---|---|---|
| 0 안전망·강제 | 🚧 | 0-B arch_guard(BE) 착수·검증 완료 / 0-A·0-C·0-D 남음 |
| 1 FE SSOT | ⬜ | |
| 2 tenant·adapter | ⬜ | |
| 3 context 재편 | ⬜ | |
| 4 god object | ⬜ | |
| 5 SEALED 정리 | ⬜ | |
| 6 승격·팀준비 | ⬜ | |

## 착수 기록

### 2026-05-22 — Phase 0-B 착수·검증 (backend arch_guard)
- `backend/tools/arch_guard/check_boundaries.py` — stdlib-only ast 경계 체커(cross_domain / infra_in_domain). import-linter 대신 ast 선택(README에 사유: Django 부트스트랩 불필요).
- `backend/tools/arch_guard/baseline.json` — 현재 위반 동결: cross_domain 523 / infra_in_domain 92 (key 496).
- `backend/tools/arch_guard/README.md` — baseline freeze→burn-down→promote 운영.
- `backend/.github/workflows/arch-guard.yml` — PR/feature push 게이트(배포 파이프라인 독립).
- 검증(실측): 평시 검사 exit 0(신규 0). baseline 1건 임시 제거 → 신규 1건 file:line 검출 + exit 1. 복원 후 exit 0.
- 남음: 0-A characterization test(무테스트 5도메인 + 분해 예정 god object), 0-C FE eslint-boundaries/dependency-cruiser baseline, 0-D drift 가시화.
