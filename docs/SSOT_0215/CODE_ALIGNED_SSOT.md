# 코드 기계 정렬 SSOT (실제 코드와 일치만 기록)

**역할**: `AWS_500_START_DEPLOY_GUIDE.md`, `HEXAGONAL_10K_EXECUTION_PLAN_v1.5.md` 두 문서는 **원본 보존(수정 없음)**.  
본 문서는 **실제 코드·grep 결과와 기계적으로 일치하는 검증만** 최소 분량으로 기록.

---

## 1. 문서 구성 (최종 3개)

| 파일 | 용도 |
|------|------|
| `AWS_500_START_DEPLOY_GUIDE.md` | 500명 스타트 배포 절차·스펙·체크리스트 (원본) |
| `HEXAGONAL_10K_EXECUTION_PLAN_v1.5.md` | Hexagonal·10K 실행 계획·게이트·Phase (원본) |
| `CODE_ALIGNED_SSOT.md` | 본 문서. 코드 경로·grep·상수·스크립트만 |

---

## 2. 검증 명령 → 통과 기준 (실행 시 재확인)

### 2.1 USE_LEGACY_AI_WORKER 0건

```bash
grep -r "USE_LEGACY_AI_WORKER" --include="*.py" .
```

**통과**: 출력 0건 (또는 docs/·비코드 제외 시 *.py 0건).

---

### 2.2 Worker 내 상태 전이 직접 호출 0건

```bash
grep -rn "mark_processing\|complete_job\|fail_job" apps/worker --include="*.py"
```

**통과**: No matches found. (use case·repository 경유만 사용.)

---

### 2.3 academy/domain, academy/application 에 django import 0건

```bash
grep -r "import django\|from django" academy/domain academy/application --include="*.py"
```

**통과**: 0건.

---

### 2.4 Worker·academy 내 .objects. 위치

- **apps/worker**: `.objects.` 0건.
- **academy**: `.objects.` 는 `academy/adapters/db/django/` 내부만 (repositories_*.py).

```bash
grep -r "\.objects\." apps/worker --include="*.py"
# → 0건

grep -r "\.objects\." academy --include="*.py" | grep -v academy/adapters
# → 0건
```

---

### 2.5 AI 상수 (코드 위치)

| 상수 | 값 | 파일:행 |
|------|-----|--------|
| LEASE_SECONDS | 3540 | `academy/framework/workers/ai_sqs_worker.py`:40 |
| SQS_VISIBILITY_TIMEOUT | 3600 | `academy/framework/workers/ai_sqs_worker.py`:34 |
| INFERENCE_MAX_SECONDS | 3600 | `academy/framework/workers/ai_sqs_worker.py`:42 |

AI 큐 생성 스크립트: `scripts/create_ai_sqs_resources.py` 29·35·41행 `visibility_timeout": "3600"`.

---

### 2.6 AI Job DB 제약

- `apps/domains/ai/models.py`: `job_id` unique (17행), `AIResultModel.job` OneToOneField (100–105행).

---

### 2.7 Worker·Adapter 경로 (실제 사용)

| 역할 | 경로 |
|------|------|
| Messaging 로그 생성 | `academy/adapters/db/django/repositories_messaging.py` → `create_notification_log()` |
| Video 상태 전이 | `academy/adapters/db/django/repositories_video.py` → `DjangoVideoRepository.mark_processing()` |
| AI 실패 기록 | `academy/adapters/db/django/repositories_ai.py` → `mark_failed()` (transaction.atomic 내부 호출) |

---

## 3. Gate 10 런타임 검증

**스크립트**: `scripts/gate10_test.py`  
**실행**: `.env` 로드 후 `python scripts/gate10_test.py` (DJANGO_SETTINGS_MODULE=apps.api.config.settings.base).

**7단계**: (1) Tenant·User·Lecture 생성 (2) AI job_id 중복 → IntegrityError (3) create_notification_log·DjangoVideoRepository.mark_processing (4) Lease 3540초 계산 (5) mark_failed → job.status=FAILED.

**통과**: 각 단계 [PASS], 최종 출력 `**[GO]** (Big Bang GO)`.

---

## 4. 갱신 원칙

- 원본 두 문서는 단일 진실로 **수정하지 않음**.
- 본 문서는 **grep·경로·상수·스크립트 결과만** 반영. 코드 변경 시 위 검증 명령 재실행 후 결과만 갱신.
