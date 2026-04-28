# Hexagonal Cutover Policy (`backend/academy/` ↔ `backend/apps/`)

**Status:** Active · V1.1.1
**Owners:** Backend
**Last reviewed:** 2026-04-28

`backend/academy/`(헥사고날) 와 `backend/apps/`(Django apps) 가 공존한다. 이 문서는 **신규 코드의 배치 규칙**과 **기존 코드의 이관 정책**을 정의한다.

---

## 1. 배경

- 2026-04-13 대규모 리팩토링으로 헥사고날 레이어(`academy/`) 도입.
- 도입 직후 `academy/` 사용 범위가 점진 확대됨 (현재 `from academy.` import ≈ 200건).
- 그러나 두 트리에 **같은 도메인 이름이 동시에 존재** (`academy/domain/ai/` vs `apps/domains/ai/`, `academy/domain/tools/` vs `apps/domains/tools/` 등).
- 신규 변경 시 "어디에 넣어야 하는가"가 매번 판단 비용이 되어, 이 문서로 단일 규칙을 박는다.

이행기 비용을 감수하는 대신, **레이어별 책임 경계가 명확하게 분리**되도록 한다.

---

## 2. 레이어 책임 (정식 정의)

| 레이어 | 위치 | 책임 | 의존 가능 방향 |
|--------|------|------|---------------|
| **HTTP entry** | `backend/apps/api/v1/urls.py`, `…/internal/`, `apps/api/config/` | URL 라우팅, settings, asgi/wsgi | → domains, support, core |
| **Domain (Django CRUD)** | `backend/apps/domains/<domain>/` | Django Model · Migration · Serializer · Admin · 단순 View · 단순 Service | → core, shared, support, **academy.adapters** (read-only repository로만), **academy.application.use_cases** |
| **Cross-cutting service** | `backend/apps/support/{ai,messaging,video,analytics}/` | HTTP 경로에서 쓰는 횡단 서비스 (전송/디스패치/통계) | → domains, core, shared, academy.* |
| **Worker entry** | `backend/apps/worker/{ai_worker,video_worker,messaging_worker}/` | SQS poll loop, batch entrypoint, daemon main | → academy.application.use_cases, academy.adapters |
| **Core platform** | `backend/apps/core/` | tenant 미들웨어, 인증, 기본 권한, 시그널, parsing | (no upward import — 모든 곳에서 import 가능) |
| **Shared (no models)** | `backend/apps/shared/{contracts,utils}/` | DTO·계약·순수 유틸 (Django model 금지) | (의존 없음) |
| **Infrastructure shim** | `backend/apps/infrastructure/storage/` | 레거시 R2 어댑터 (점진 이관 대상 → `academy/adapters/storage/`) | → academy.adapters |
| **Domain (pure)** | `backend/academy/domain/` | 프레임워크-자유 엔티티·VO·도메인 에러 | (Django/외부 라이브러리 import 금지) |
| **Application ports** | `backend/academy/application/ports/` | 인터페이스(추상). DIP 경계 | → academy.domain |
| **Application services / use cases** | `backend/academy/application/{services,use_cases,video}/` | 오케스트레이션, 트랜잭션 경계 | → academy.domain, academy.application.ports |
| **Adapters** | `backend/academy/adapters/{db,cache,storage,queue,ai,video,tools}/` | 인프라 구현 (Django ORM, Redis, R2, SQS, OpenCV 등) | → academy.application.ports, **apps.domains.\<x\>.models** (read/write 시 Django ORM 사용) |
| **Worker framework** | `backend/academy/framework/workers/` | SQS worker glue (visibility extension, dispatch) | → academy.application, academy.adapters |
| **Standalone module** | `backend/apps/billing/` | 결제 모듈 (도메인 분류 없이 top-level 유지) | → core, shared |

### 핵심 규칙
- `academy/domain/` 은 Django 포함 외부 의존을 **import 하지 않는다**. 어기면 헥사고날 의미가 사라진다.
- `academy/adapters/db/django/` 는 예외적으로 `apps.domains.<x>.models` 를 import 한다 (Django ORM이 어댑터 구현이므로).
- `apps/domains/<x>/` 는 다른 도메인의 model을 직접 import 하지 않는다 — 필요하면 `academy.adapters.db.django.repositories_<x>` 또는 `apps/support/`로 우회.
- `apps/worker/<x>_worker/` 는 비즈니스 로직을 담지 않는다. SQS 메시지 → use case 호출이 끝.

---

## 3. 신규 코드 배치 결정 트리

```
신규 변경이 들어올 때:

1) HTTP 엔드포인트 추가/변경?
   → apps/domains/<domain>/{urls,views,serializers}.py
   → DB 모델 변경 동반 시 같은 도메인의 models/, migrations/

2) 비동기/SQS/배치/장기실행?
   → academy/application/use_cases/<area>/  (오케스트레이션)
   → academy/adapters/<infra>/             (외부 호출 구현)
   → apps/worker/<x>_worker/               (entry만)

3) 외부 인프라 (R2/SQS/Redis/AI API/FFmpeg) 호출?
   → academy/adapters/<infra>/

4) 횡단 도메인 (메시지 발송, 영상 트리거, AI 디스패치)?
   → apps/support/{messaging,video,ai,analytics}/

5) 순수 계산/DTO/유틸 (Django 의존 없음)?
   → apps/shared/utils/  또는  academy/domain/shared/

6) 새 도메인 자체를 도입?
   → apps/domains/<new_domain>/  (모델·CRUD·serializer)
   → 외부 인프라 호출이 있으면 academy/adapters/db/django/repositories_<new_domain>.py 동시 작성
```

---

## 4. 이관(Migration) 정책

**Freeze 대상** (신규 진입 금지, 점진 이관):
- `backend/apps/infrastructure/storage/r2_adapter.py` → 사용처는 `academy/adapters/storage/r2_adapter.py`로 이관 후 제거.
- `backend/apps/domains/<x>/services/` 중 외부 인프라(R2/AI/SQS)를 직접 호출하는 모듈 → `academy/adapters/`로 어댑터 이관 후 use case 호출 패턴으로 변경.

**유지 대상** (영구 공존):
- `apps/domains/<x>/{models,migrations,serializers,admin,urls,views}.py` — Django CRUD는 그대로 둔다. 헥사고날로 옮길 가치 없음 (오버엔지니어링).
- `apps/core/`, `apps/shared/`, `apps/billing/` — 현 위치 유지.

**금지**:
- `academy/domain/` 안에 Django model · migrations · serializer 작성 금지.
- `apps/domains/<x>/` 안에 SQS / Redis / R2 / AI API 직접 호출 금지 (어댑터 우회).
- 두 트리에 같은 책임의 코드 중복 작성 금지 — 한쪽이 정본이면 다른 쪽은 import만.

---

## 5. 도메인 이름 충돌 처리

현재 충돌:

| 이름 | `academy/` 쪽 책임 | `apps/domains/` 쪽 책임 | 정책 |
|------|-------------------|------------------------|------|
| `ai` | `domain/ai/entities,errors` + `adapters/ai/sqs_adapter` + `application/use_cases/ai/` (오케스트레이션) | `models, gateway, services, queueing, callbacks` (HTTP/모델/디스패치) | **공존 OK** — 책임 분리됨 (오케스트레이션 vs HTTP/모델). 신규 인프라 호출은 `academy/`, 신규 HTTP/모델은 `apps/`. |
| `tools` | `domain/tools/{image_preprocessor, ppt_composer, question_splitter}` (순수 알고리즘) + `adapters/tools/{pptx_writer, pymupdf_renderer}` | `ppt/, services/, timer_*` (HTTP 뷰 + 도메인 서비스) | **공존 OK** — academy 쪽은 알고리즘 코어, apps 쪽은 HTTP 표면. |
| `video` | `application/video/handler.py` + `adapters/video/processor.py` | `apps/support/video/`, `apps/worker/video_worker/` | **공존 OK** — academy 쪽이 정본 처리, support/worker는 entry. |

→ 충돌이 아니라 **레이어 분담**이다. 위 표에서 정의한 경계를 지키면 된다.

---

## 6. 검증 (PR 리뷰 기준)

- [ ] `academy/domain/` 안에 `from django` / `import django` 없음
- [ ] `apps/domains/<x>/` 안에 `boto3` / `redis.Redis` / `r2_client` / `requests` 직접 호출 없음 (어댑터 경유)
- [ ] 신규 SQS/배치 진입점은 `apps/worker/<x>_worker/` 에만 있고, 본문은 `academy/application/use_cases/` 호출
- [ ] 신규 도메인 도입 시 `apps/domains/<new>/` + (필요 시) `academy/adapters/db/django/repositories_<new>.py` 동시 추가

---

## 8. 평가(Assessment) 5도메인 책임 분담

`apps/domains/` 안의 평가 관련 5도메인은 이름이 비슷해 경계가 자주 헷갈린다. 책임을 한 표에 박는다.

| 도메인 | 책임 (한 줄) | 모델 | 비책임 |
|--------|------------|------|-------|
| `exams` | **출제(question authoring) SSOT** — 시험 정의·자산·템플릿 | Exam, Sheet, ExamQuestion, AnswerKey, ExamAsset, ExamEnrollment, QuestionExplanation, TemplateBundle | 답안·채점·결과·집계 |
| `submissions` | **답안(answers) SSOT + 제출 상태머신** — 채점 미경유 | Submission(state: SUBMITTED→ANSWERS_READY→GRADING→DONE), SubmissionAnswer | 점수 계산·정답 판정 (results 호출만) |
| `results` | **🔒 시험 결과 SSOT (SEALED)** — exam-only, 집계의 유일 책임지 | Result(FK=enrollment_id), ExamAttempt, ResultFact, ResultItem, ExamResult, ScoreEditDraft, WrongNotePDF | 숙제 결과 (homework_results 소관), 답안 (submissions 소관) |
| `homework` | **숙제 정책·배정** | HomeworkPolicy, HomeworkEnrollment, HomeworkAssignment | 숙제 정의·점수 (homework_results 소관) |
| `homework_results` | **숙제 정의 + 점수 스냅샷** ⚠️ 이름 오해 주의 — "results"지만 본체는 Homework 정의. | Homework, HomeworkScore | 채점 (직접 점수 입력, Submission 미경유) |

### 핵심 경계 (반드시 지킬 것)

- `submissions` 안에 **점수 계산 금지**. 채점은 `results.exam_grading_service.grade_submission()` 호출.
  → `submissions/services/dispatcher.py` 가 그 단일 경계점.
- `results` 안에 **Homework 관련 코드 추가 금지**. results 는 exam-only.
  → 숙제 결과는 `homework_results.HomeworkScore` 로.
- `exams` 와 `homework` 모두 `template` 형(template_exam / template_homework self-FK + TemplateBundle 다리)을 갖는다.
  → 둘 사이를 직접 import 하지 말고 `apps/domains/exams/models/template_bundle.py` 가 polymorphic 다리.
- `homework_results.HomeworkScore` 의 직접 점수 갱신은 `homework_results.HomeworkScoreViewSet` 이 단일 책임자.
  → URL prefix `/api/v1/homework/scores/` 는 프런트 호환 유지를 위해 `homework.urls` 에서 cross-domain view import 로 라우팅. 모델/serializer/filter/service 는 모두 homework_results 안에 있다.
- HomeworkPolicy 변경 시 HomeworkScore 재계산은 `homework_results.services.policy_recalc.recalc_scores_for_policy_change()` 단일 진입.
  → `homework.HomeworkPolicyViewSet.partial_update` 에서 호출만 한다.

### 알려진 부채

- **`homework` ↔ `homework_results` 분리는 자의적**. URL prefix 가 `/api/v1/homework/` 와 `/api/v1/homeworks/` 두 개로 갈라져 있어 프런트가 매번 헷갈리는 실질 부담. 향후 multi-PR 통합 예정 (코드 위치 + URL 통합 + frontend api.ts 갱신).
- **`results` 도메인 이름**은 "통합 결과"로 오해되기 쉽지만 실체는 exam-only. SEALED 라 rename 불가. README.md 와 본 표로 명문화하는 것이 답.

### 신규 변경 가이드

- 시험 출제: `apps/domains/exams/` 만.
- 답안 입력: `apps/domains/submissions/` 만 (채점은 호출하지 말고 dispatcher 사용).
- 시험 채점·집계: `apps/domains/results/` (봉인 — 구조 변경 금지).
- 숙제 정책·배정: `apps/domains/homework/`.
- 숙제 정의·점수: `apps/domains/homework_results/` (당분간).

---

## 9. 변경 이력

- **2026-04-28**: 최초 작성. 이행기 모호함 해소 목적.
- **2026-04-28**: `apps/worker/omr/` 제거 (entry 없는 라이브러리였음). `warp.py`/`roi_builder.py`는 `apps/worker/ai_worker/ai/omr/` 로 이관, `template_meta.py` 는 dead code로 삭제. 이로써 `ai_worker ↔ omr` 양방향 cross-worker import 사이클 제거.
- **2026-04-28**: 평가 5도메인 audit. `homework/views/homework_score_viewset.py` + `homework/filters.py` + `homework/serializers/core` 의 HomeworkScore 부분을 `homework_results/` 로 이관. HomeworkPolicy 재계산 로직은 `homework_results/services/policy_recalc.py` 로 분리. URL `/api/v1/homework/scores/` 와 `/api/v1/homeworks/` 모두 보존(프론트 영향 0). HomeworkQuickPatchSerializer 중복 제거(meta_status 인터페이스를 master로). Migration 0건.
- **2026-04-28**: §8 평가 5도메인 책임 분담 표 추가. results 도메인 명칭 오해 + homework/homework_results 자의적 분리 부채 명문화. 머지/분리 audit 결과 (옵션 A 즉시 적용 + 옵션 B multi-PR 후속).
