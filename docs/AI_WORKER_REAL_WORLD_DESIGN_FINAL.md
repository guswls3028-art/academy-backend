# AI 워커 실무 시나리오 기반 설계 보고서 (최종판)

**문서 버전:** 2.1 Final (단계별 적용 전략)  
**작성일:** 2026-02-14  
**최종 수정:** 2026-02-14  
**상태:** ✅ 프로덕션 완성형 (단계별 확장 가능)

---

## 📋 실행 요약 (Executive Summary)

본 보고서는 학원 운영 실무 시나리오(A: OMR 스캔 시험, B: 온라인 과제 제출, C: 비규격 답안지)를 지원하기 위한 AI 워커 설계를 제시한다.

**핵심 결론:**
- ✅ **전체 설계는 진행 가능** - Tier 시스템, Job Type 분리, CPU/GPU 전략 모두 적절
- ⚠️ **구현 전 필수 보강 3가지** 반드시 적용 필요 (입력 품질 게이트, 유무 판단 다중 신호, Dispatcher 모듈화)
- 🔥 **10K 규모 대비 필수:** Job Type별 Queue 분리, Audit Trail 데이터 분리, Auto-Scaling 전략, Idempotency
- 📅 **단계별 적용 전략:** 첫 1개월은 최소 구성, 3개월차부터 10K 대비 완전 구조

**단계별 적용 시나리오:**
- **Phase 0:** 소규모 시작, 확장 가능한 구조만 구축 (트래픽 지표 기준 전환)
- **Phase 1:** 점진적 기능 추가, 운영 안정화 (트래픽 지표 기준 전환)
- **Phase 2:** 10K 대비 완전 구조 적용 (트래픽 지표 기준 전환)

**⚠️ 중요:** 시간 기준이 아닌 트래픽 지표 기반으로 Phase 전환 결정

**10K 환경 전제:**
- 학원 50~150곳, 동시 시험 시간대, 시험 날 OMR 폭주, 숙제 제출 마감 직전 업로드 폭주
- **피크 트래픽 대응**이 핵심

**핵심 원칙:**
1. **라이트/베이직:** CPU 워커에서 완벽 처리 보장 (실패 없음)
2. **프리미엄:** GPU 워커 자동 전환, 고급 기능 제공
3. **기능 경량화 우선:** CPU에서 처리 가능하도록 최적화
4. **프리미엄 격상:** CPU 불가능한 기능은 프리미엄으로 분리

**📌 문서 정합성 원칙 (신규 개념 도입 전 필수):**
- **기존 도메인/서포트를 먼저 확인** 후, 이미 구현된 필드·개념은 그대로 사용한다.
- 예: 식별자 8자리 → Student **omr_code** (`apps/domains/students/models.py`), 워커 **identifier** / **status** (`apps/worker/ai_worker/ai/omr/identifier.py`). 식별자 미매칭 → **Submission.Status.NEEDS_IDENTIFICATION**, **meta.manual_review** (`apps/domains/submissions/`).
- 중복 필드/용어를 만들지 않고, 설계 문서와 실제 코드 변수명·상태값을 일치시킨다.

---

## 1. 실무 시나리오 요구사항

### 시나리오 A: 일반 시험 (OMR 스캔)
- **워크플로우:** 과제 제출 → OMR 시험 → 스캔 업로드 → **AI CPU 워커 자동 채점** → 서술형 이미지 제공
- **핵심 요구:** OMR 자동 채점 ✅, 식별자 인식 ✅, **서술형 이미지 추출** ⚠️

### 시나리오 B: 온라인 과제 제출
- **워크플로우:** 학생 사진/동영상 제출 → **CPU 워커 유무 판단** (채점/풀이/답안) → 정책 기반 성취도 계산
- **핵심 요구:** 유무 판단 정확도 중요, 논리적 사고 불필요, **다중 신호 기반 점수화** 필요

### 시나리오 C: 비규격 답안지
- **워크플로우:** 촬영물 업로드 → OMR 인식 → 자동 채점
- **핵심 요구:** CPU 가능 시 CPU, 불가능 시 **프리미엄(GPU)으로 분리**

---

## 2. 핵심 설계 결정사항

### 2.1 Job Type 확장

```python
AIJobType = Literal[
    "ocr",
    "omr_grading",                    # A/C: OMR 자동 채점
    "essay_answer_extraction",        # A/C: 서술형 답안지 추출
    "homework_photo_analysis",        # B: 사진 기반 과제 분석
    "homework_video_analysis",        # B: 동영상 기반 과제 분석
    "omr_video_extraction",          # C: 동영상에서 OMR 이미지 추출
    # ... 기존 타입들
]
```

### 2.2 Tier별 처리 전략

| Tier | 허용 Job Type | 처리 방식 | 실패 정책 |
|------|--------------|-----------|----------|
| **Lite** | OCR만 | CPU | - |
| **Basic** | `omr_grading` (scan만), `homework_*`, `essay_answer_extraction` | CPU 완벽 처리 | **실패 없음 보장** (Pre-Validation 필수) |
| **Premium** | 모든 타입, 모든 mode | GPU 가속 | 자동 전환 가능 |

**핵심:** Basic에서 `omr_grading`의 `mode="scan"`만 허용, 촬영물(`photo`/`video`)은 거부 → CS 감소

### 2.3 입력 품질 게이트 (Pre-Validation Layer)

**위치:** API 서버 (AIJob 생성 전)

**원칙:** Lite/Basic에서 "실패 없음"을 만들려면 **거부 기준이 운영 문장으로 고정**되어야 함. 거부 사유는 **프론트에서 그대로 사용자 안내 문구로 노출** 가능해야 함.

**거부 정책 (운영 문장 — 코드/문서 일치 권장):**

| 코드 | 거부 조건 | 사용자 노출 문구 예시 |
|------|-----------|------------------------|
| `RESOLUTION_TOO_LOW` | 해상도 최소 미달 (예: 짧은 변 600px 미만) | "해상도가 낮습니다. 더 선명하게 촬영해 주세요." |
| `FILE_TOO_LARGE` | 용량 초과 (job_type별 상한) | "파일 크기가 제한을 초과했습니다." |
| `VIDEO_TOO_LONG` | 동영상 길이 초과 | "동영상 길이 제한을 초과했습니다." |
| `BLUR_OR_SHAKE` | 흔들림/블러 과다 | "흔들리거나 흐릿합니다. 고정해서 다시 촬영해 주세요." |
| `TOO_DARK` | 밝기 부족 | "너무 어둡습니다. 밝은 곳에서 촬영해 주세요." |
| `INVALID_FORMAT` | 포맷 미지원 | "지원하지 않는 파일 형식입니다." |
| `OMR_PHOTO_NOT_ALLOWED` | Basic에서 OMR 촬영물(mode=photo/video) | "Basic 요금제에서는 스캔된 OMR만 가능합니다. 촬영물은 Premium에서 이용해 주세요." |

**검증 항목 요약:** 해상도, 용량, 길이, 흔들림, 밝기, 포맷, **Basic 시 OMR 촬영물 거부**.

**함수 시그니처:**
```python
def validate_input_for_basic(
    tier: str,
    job_type: str,
    payload: dict
) -> tuple[bool, Optional[str], Optional[str]]:
    """
    Returns: (ok: bool, error_message?: str, rejection_code?: str)
    rejection_code: 프론트 매핑용 (RESOLUTION_TOO_LOW 등)
    """
```

### 2.4 유무 판단 정확도 전략 (다중 신호 기반)

**문제:** 단일 알고리즘(색상 히스토그램만)은 연필 채점, 연한 형광펜, 배경 노이즈에서 오판

**해결:** 룰 기반 다중 점수 시스템

| 항목 | 단순 방식 (피하기) | 권장 방식 |
|------|-------------------|----------|
| **채점** | 빨간색 픽셀만 | 색상 + **원형/V자 컨투어** (HoughCircles, Shape Matching) |
| **풀이** | 텍스트 길이만 | 텍스트 **밀도·분산** + **Laplacian 엣지 강도** |
| **답안** | OCR만 | 짧은 텍스트/숫자 + 위치 고정 특성 |

**점수화 예시:**
```python
grading_score = (
    red_color_score * 0.4 +
    checkmark_pattern_score * 0.3 +
    mark_cluster_score * 0.3
)
has_grading = (grading_score > threshold)
```

**confidence voting:** 여러 영역/프레임에서 일관성 검증으로 정확도 향상

**B(과제 유무판단) 정확도 보강 3종 세트:**
1. **다중 신호 점수화:** 위와 동일 (색상 + 컨투어 + 밀도 등).
2. **유무 판정 히스테리시스:** 단일 임계값 대신 on/off 구분으로 튐 방지.
   - 예: `has_solution`은 `on_threshold`(예: 0.65) / `off_threshold`(예: 0.45) 적용. 현재 값이 high 구간이면 0.45 미만까지 내려와야 OFF, low 구간이면 0.65 이상 올라와야 ON.
3. **동영상 top-k 투표:** "최고 프레임 1장"만 쓰지 말고 **top-k(예: 3~5장) 투표**로 유무 판정 → CPU에서도 체감 정확도 향상.

```python
# 유무 판정 히스테리시스 예시
def has_solution_with_hysteresis(score: float, state: str, on_threshold=0.65, off_threshold=0.45) -> tuple[bool, str]:
    if state == "high":
        return (score >= off_threshold, "high" if score >= off_threshold else "low")
    else:
        return (score >= on_threshold, "high" if score >= on_threshold else "low")

# 동영상: top-k 프레임 투표
def has_grading_from_video(frames_scores: list[float], k=5) -> bool:
    top_scores = sorted(frames_scores, reverse=True)[:k]
    return sum(1 for s in top_scores if s > 0.5) >= (k // 2 + 1)
```

### 2.5 Dispatcher 모듈화 구조

**문제:** 모든 로직이 `dispatcher.py`에 집중 → 유지보수 지옥

**해결:** 도메인별 모듈 분리

```
apps/worker/ai_worker/ai/pipelines/
├── omr/
│   ├── grading.py           # omr_grading
│   ├── essay_extractor.py   # essay_answer_extraction
│   └── video_extractor.py   # omr_video_extraction
├── homework/
│   ├── photo_analyzer.py     # homework_photo_analysis
│   └── video_analyzer.py     # homework_video_analysis
└── dispatcher.py            # job_type → handler 라우팅만
```

**dispatcher 역할:** 오직 라우팅만
```python
def handle_ai_job(job: AIJob) -> AIResult:
    handlers = {
        "omr_grading": omr.grading.handler,
        "essay_answer_extraction": omr.essay_extractor.handler,
        "homework_photo_analysis": homework.photo_analyzer.handler,
        # ...
    }
    return handlers[job.type](job)
```

---

## 3. 기능별 상세 설계

### 3.1 서술형 답안지 추출 (`essay_answer_extraction`)

**목적:** OMR 답안지에서 서술형 영역만 추출하여 이미지 제공

**핵심 설계:**
- **앵커 포인트:** 템플릿 설계 시 서술형 영역 4점 좌표 정의
- **스캔 vs 촬영:** 촬영본은 **Perspective Transform** 적용 후 정규화 좌표 사용
- **여유값(Padding):** 좌표값대로만 자르면 글씨 끝부분이 잘림 → 바운딩 박스 추출 시 **상하좌우 5~10% 패딩** 추가 (조교 가독성 향상)
- **출력:** 각 서술형 문항별 이미지 URL + 바운딩 박스 (패딩 포함)

**Tier:** Basic (스캔 파일), Premium (촬영물)

#### 3.1.1 식별자 8자리 매칭 (1급 시민) — 기존 구현 정합성

**목적:** A(스캔 OMR)에서 식별자 인식 실패/불확실을 운영 상태로 명시 → CS 감소. essay_answer_extraction과 동일한 사용자 체감 SLA.

**⚠️ 기존 도메인/워커와 필드명 통일 (중복·정합성 방지):**

| 구분 | 실제 구현 위치 | 필드/개념 | 비고 |
|------|----------------|-----------|------|
| **학생 식별자(8자리)** | `apps/domains/students/models.py` L41-46 | **`omr_code`** | Student.omr_code (전화번호 뒤 8자리) |
| **워커 인식 결과** | `apps/worker/ai_worker/ai/omr/identifier.py` | **`identifier`** (dict) | detect_identifier_v1 반환 |
| **인식된 8자리 문자열** | 워커 result.identifier | **`identifier`** (str \| None) | "12345678" 또는 None |
| **인식 신뢰도** | 워커 result.identifier | **`confidence`** | 0.0~1.0 |
| **인식 상태** | 워커 result.identifier | **`status`** | "ok" \| "ambiguous" \| "blank" \| "error" |
| **제출물 식별자 미매칭** | `apps/domains/submissions/models/submission.py` | **`Status.NEEDS_IDENTIFICATION`** | 식별 실패 시 Submission 상태 |
| **수동 검토 플래그** | submission.meta | **`manual_review.required`**, **`manual_review.reasons`** | ai_omr_result_mapper에서 설정 |

**OMR 결과 payload (기존 워커 계약 유지):**

| 필드 | 타입 | 설명 | 구현 |
|------|------|------|------|
| **`identifier`** | str \| None | 인식된 8자리 (Student.omr_code와 매칭 대상) | identifier.py |
| **`raw_identifier`** | str | '?' 포함 가능 (디버그/리트라이용) | identifier.py |
| **`confidence`** | float | 식별자 인식 신뢰도 (0.0~1.0) | identifier.py |
| **`status`** | str | **"ok"** \| **"ambiguous"** \| **"blank"** \| **"error"** | identifier.py |

**status → 운영 의미 매핑 (문서/UI용):**

- **ok** → 매칭 성공 가능 (API에서 omr_code로 Student 조회 후 enrollment_id 설정)
- **ambiguous** / **blank** / **error** → 자동 점수 반영 금지 → **Submission.Status.NEEDS_IDENTIFICATION** + **manual_review.required** (이미 `apps/domains/submissions/services/ai_omr_result_mapper.py` 반영)

**구현:** 기존 워커는 `identifier`, `confidence`, `status` 이미 반환. API/매퍼에서 `status != "ok"` 또는 enrollment 매칭 실패 시 NEEDS_IDENTIFICATION + 조교 매칭 큐 노출. 신규 필드 추가 없이 기존 계약만 문서화·운영 정책으로 정리.

### 3.2 과제 사진 분석 (`homework_photo_analysis`)

**목적:** 채점/풀이/답안 작성 여부 유무 판단 (논리적 사고 불필요)

**핵심 설계:**
- **다중 신호 점수화:** 색상 + 컨투어 + 밀도 분석
- **출력:** 
  - `has_grading` (boolean) + `grading_confidence` (0.0~1.0)
  - `has_solution` (boolean) + `solution_confidence` (0.0~1.0)
  - `has_answer` (boolean) + `answer_confidence` (0.0~1.0)
  - 미완성 문항 리스트
- **정확도:** confidence voting으로 향상
- **활용:** confidence 점수로 "확신이 없는 경우만 조교에게 알림" 기능 구현 가능

**Tier:** Basic (CPU 경량 알고리즘)

### 3.3 동영상 OMR 추출 (`omr_video_extraction`)

**목적:** 동영상에서 최적 프레임 추출

**핵심 설계:**
- **모션 블러 대응:** 샘플링된 프레임 중 **Laplacian Variance가 가장 높은 프레임** 우선 선택
- **품질 점수:** 선명도(0.4) + 명도(0.25) + 정렬(0.2) + OMR 패턴(0.15)
- **타임아웃 동적 조절:** 동영상 파일 용량이 크므로 S3 다운로드 시간이 병목 → 파일 크기에 비례하여 타임아웃 동적 조절 (예: 100MB당 +30초)

**Tier:** Basic (시도), Premium (보장)

### 3.4 과제 동영상 분석 강화 (`homework_video_analysis`)

**개선 방향:**
- 기존 키 프레임 추출 활용
- 각 프레임에서 사진 분석과 동일한 다중 신호 점수화 적용
- 여러 프레임에서 일관성 검증

**Tier:** Basic (CPU)

---

## 4. 운영 설계 (프로덕션 완성형)

### 4.1 Job 상태 머신 (State Machine)

**문제:** 현재는 "처리한다"만 있음. 실제 운영에서는 상태 추적이 필수.

**상태 정의 (운영 친화형):**

```
PENDING
  ↓ (Pre-Validation 시작)
VALIDATING
  ↓ (검증 성공)
PROCESSING
  ↓ (검증 실패: 거부 정책 해당)
REJECTED_BAD_INPUT   ← Lite/Basic 실패 없음: "거부 or 성공"만 허용
  ↓ (처리 성공)
SUCCESS              ← Lite/Basic 애매 시: SUCCESS + flags.review_candidate=true (Shadow)
  ↓ (처리 실패, Premium만)
FAILED
  ↓ (Basic validation/처리 실패 → Premium 격상)
FALLBACK_TO_GPU
  ↓ (재시도 필요 시)
RETRYING
  ↓ (Premium/조교 큐 전용)
REVIEW_REQUIRED      ← Lite/Basic에는 노출 안 함. Lite/Basic은 SUCCESS+review_candidate
```

**정책 요약 (CPU 실패 없음):**
- **Lite/Basic:** FAILED를 가능한 한 없앰. (1) 거부 가능한 케이스 → **REJECTED_BAD_INPUT** (명확한 사용자 액션 유도), (2) 그 외 → **항상 SUCCESS**로 응답하되 confidence 낮으면 **REVIEW_CANDIDATE**로만 적재 (Shadow로 시작). REVIEW_REQUIRED는 Premium 또는 내부 조교 큐용으로만 노출.
- **Premium:** 실패/애매/특정 에러는 GPU 재시도·강화 루트로 흡수. GPU 실패도 사실상 금지에 가깝게 설계 (재시도/프레임 재선정/가이드/최종 REVIEW 루트).

**REVIEW_REQUIRED vs review_candidate:**
- **REVIEW_REQUIRED:** Premium 또는 내부 운영(조교 큐)에서만 사용. 실제 검토 큐에 노출.
- **Lite/Basic 애매:** SUCCESS + `flags.review_candidate=true` (Shadow Mode에서 로그만, 조교 큐 비노출).

**상태 전이 규칙:**

| 현재 상태 | 이벤트 | 다음 상태 | 비고 |
|----------|--------|----------|------|
| PENDING | Pre-Validation 시작 | VALIDATING | - |
| VALIDATING | 검증 성공 | PROCESSING | - |
| VALIDATING | 검증 실패 (거부 정책 해당) | **REJECTED_BAD_INPUT** | Lite/Basic/Premium 공통. 거부 사유는 프론트 노출 가능 |
| VALIDATING | 검증 실패 (Basic, Premium 격상 가능) | FALLBACK_TO_GPU | Premium으로 자동 승격 |
| VALIDATING | 검증 실패 (Premium) | FAILED | - |
| PROCESSING | 처리 성공 | SUCCESS | - |
| PROCESSING | Confidence 낮음 (Lite/Basic) | **SUCCESS** | payload.flags.review_candidate=true (REVIEW_REQUIRED 아님) |
| PROCESSING | Confidence 낮음 (Premium) | REVIEW_REQUIRED | 조교 검토 큐 노출 |
| PROCESSING | 처리 실패 (재시도 가능) | RETRYING | max_attempts 확인 |
| PROCESSING | 처리 실패 (재시도 불가, Basic) | FALLBACK_TO_GPU | Premium이면 Fallback 시도 |
| PROCESSING | 처리 실패 (재시도 불가, Premium) | FAILED | - |
| RETRYING | 재시도 성공 | PROCESSING | - |
| RETRYING | 재시도 실패 (Basic) | FALLBACK_TO_GPU | - |
| RETRYING | 재시도 실패 (Premium) | FAILED | - |
| FALLBACK_TO_GPU | GPU 처리 시작 | PROCESSING | Premium 큐로 이동 |
| REVIEW_REQUIRED | 조교 검토 완료 | SUCCESS | 수동 승인 |

**Fallback 정책 (명시):**
- **검증 실패:** Basic → Premium Fallback (검증 단계에서 차단)
- **처리 실패:** Basic processing 실패도 Premium이면 Fallback 시도 (단, 비용 제어 조건 통과 시)
  - 라이브러리 에러, 손상 파일, 시간초과 등 특정 에러 타입은 Fallback
  - Confidence 기반 Fallback도 가능

**구현 위치:** `apps/domains/ai/models.py` (AIJobModel.status 필드 확장)

**상태 코드:**
```python
class AIJobStatus(models.TextChoices):
    PENDING = "PENDING", "PENDING"
    VALIDATING = "VALIDATING", "VALIDATING"
    PROCESSING = "PROCESSING", "PROCESSING"
    SUCCESS = "SUCCESS", "SUCCESS"
    FAILED = "FAILED", "FAILED"
    REJECTED_BAD_INPUT = "REJECTED_BAD_INPUT", "REJECTED_BAD_INPUT"  # 거부 정책 해당, 사용자 액션 유도
    FALLBACK_TO_GPU = "FALLBACK_TO_GPU", "FALLBACK_TO_GPU"
    RETRYING = "RETRYING", "RETRYING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED", "REVIEW_REQUIRED"  # Premium/조교 큐 전용. Lite/Basic은 SUCCESS+review_candidate
```

### 4.2 AI 결과 저장 전략 (Audit Trail)

**문제:** URL 반환만으로는 6개월 후 "왜 이렇게 처리됐어요?" CS 대응 불가.

**필수 저장 항목 (10K 대비 데이터 분리 전략):**

| 항목 | 저장 위치 | 용도 | 10K 대비 전략 |
|------|----------|------|--------------|
| **최종 결과** (SUCCESS/FAIL) | RDB | 빠른 조회 | 핫 데이터만 RDB |
| **원본 이미지 URL** | S3 | 원본 보관 | S3 직접 저장 |
| **전처리 이미지 URL** | S3 | 전처리 결과 | S3 직접 저장 |
| **분석 메트릭** | NoSQL/S3 JSON | 상세 분석 | **핫/콜드 분리** (Phase 2: 최근 30일 NoSQL, 이후 S3 Archive; Phase 0/1은 90일 보관) |
| **confidence score** | RDB (별도 테이블) | 유무 판단 신뢰도 | **메트릭 테이블 분리** (인덱스 최적화) |
| **처리 시간** | RDB (별도 테이블) | 성능 모니터링 | 메트릭 테이블 분리 |
| **threshold 값** | RDB | 판단 기준값 | RDB (변경 이력 포함) |
| **사용된 알고리즘 버전** | RDB | 알고리즘 변경 추적 | RDB |

**10K 대비 핵심 전략:**
1. **핫/콜드 데이터 분리:** Phase 2부터 최근 30일은 DB/NoSQL, 이후 S3 Archive. Phase 0/1은 90일 보관.
2. **메트릭 테이블 분리:** JSONField 남발 금지, 별도 정규화된 테이블 (`ai_job_metrics`)
3. **S3 직접 저장:** 이미지 URL은 RDB에만 저장, 실제 파일은 S3

**DB 스키마 예시 (10K 대비 최적화):**

```python
class AIResultModel(BaseModel):
    """최종 결과만 저장 (핫 데이터)"""
    job = models.OneToOneField(AIJobModel, ...)
    
    # 최종 결과 (최소한의 데이터만)
    payload = models.JSONField()  # 최종 결과만 (has_grading, has_solution 등)
    
    # 이미지 URL (S3 경로만)
    original_image_url = models.URLField(null=True)
    preprocessed_image_url = models.URLField(null=True)
    
    # 메타데이터
    algorithm_version = models.CharField(max_length=50, default="v1")
    
    class Meta:
        indexes = [
            models.Index(fields=["job_id"]),
            models.Index(fields=["created_at"]),  # 최근 30일 조회 최적화
        ]

class AIJobMetricsModel(BaseModel):
    """메트릭 별도 테이블 (인덱스 최적화)"""
    job = models.OneToOneField(AIJobModel, related_name="metrics")
    
    # 신호별 점수 (정규화된 컬럼)
    grading_red_color_score = models.FloatField(null=True)
    grading_checkmark_score = models.FloatField(null=True)
    grading_cluster_score = models.FloatField(null=True)
    grading_final_score = models.FloatField(null=True)
    
    solution_density_score = models.FloatField(null=True)
    solution_variance_score = models.FloatField(null=True)
    solution_laplacian_score = models.FloatField(null=True)
    solution_final_score = models.FloatField(null=True)
    
    # Confidence (인덱스 가능)
    grading_confidence = models.FloatField(null=True, db_index=True)
    solution_confidence = models.FloatField(null=True, db_index=True)
    answer_confidence = models.FloatField(null=True, db_index=True)
    
    # Threshold (변경 이력 추적)
    grading_threshold = models.FloatField(null=True)
    solution_threshold = models.FloatField(null=True)
    answer_threshold = models.FloatField(null=True)
    
    # 성능
    processing_time_seconds = models.FloatField(null=True, db_index=True)
    
    class Meta:
        db_table = "ai_job_metrics"
        indexes = [
            models.Index(fields=["grading_confidence"]),  # 검토 필요 큐 조회
            models.Index(fields=["processing_time_seconds"]),  # 성능 모니터링
        ]

# 상세 메트릭 (JSON)은 NoSQL 또는 S3에 저장
# 최근 30일: DynamoDB/MongoDB
# 이후: S3 JSON Archive
```

**CS 대응 예시:**
- "이 학생 왜 미채점 처리됐어요?"
- → `analysis_metrics.grading.final_score` 확인
- → `threshold_values.grading_threshold` 확인
- → `confidence_scores.grading` 확인
- → 원본 이미지로 재분석 가능

### 4.3 Threshold 튜닝 전략

**문제:** threshold를 하드코딩하면 학원별/과목별/채점 스타일별 조정 불가.

**해결:** threshold는 설정값으로 분리, 하드코딩 금지.

**설계:**

1. **기본값 (Default):**
   ```python
   DEFAULT_THRESHOLDS = {
       "grading": 0.6,
       "solution": 0.5,
       "answer": 0.4,
   }
   ```

2. **설정 가능한 레벨:**
   - **전역 (Global):** 모든 학원 공통 기본값
   - **학원별 (Tenant):** 학원의 채점 스타일 반영
   - **과목별 (Subject):** 과목 특성 반영
   - **선생별 (Teacher):** 선생의 채점 정책 반영

3. **우선순위:** 선생별 > 과목별 > 학원별 > 전역

**구현 위치:**

```python
# apps/domains/ai/services/threshold_manager.py

def get_threshold(
    tenant_id: str,
    subject_id: Optional[str] = None,
    teacher_id: Optional[str] = None,
    threshold_type: str = "grading"
) -> float:
    """
    우선순위: 선생별 > 과목별 > 학원별 > 전역
    """
    # 선생별 설정 확인
    if teacher_id:
        teacher_threshold = get_teacher_threshold(teacher_id, threshold_type)
        if teacher_threshold is not None:
            return teacher_threshold
    
    # 과목별 설정 확인
    if subject_id:
        subject_threshold = get_subject_threshold(subject_id, threshold_type)
        if subject_threshold is not None:
            return subject_threshold
    
    # 학원별 설정 확인
    tenant_threshold = get_tenant_threshold(tenant_id, threshold_type)
    if tenant_threshold is not None:
        return tenant_threshold
    
    # 전역 기본값
    return DEFAULT_THRESHOLDS[threshold_type]
```

**DB 스키마 (변경 이력 포함):**

```python
class ThresholdConfigModel(BaseModel):
    """Threshold 설정"""
    tenant_id = models.CharField(max_length=64, null=True, blank=True)
    subject_id = models.CharField(max_length=64, null=True, blank=True)
    teacher_id = models.CharField(max_length=64, null=True, blank=True)
    
    threshold_type = models.CharField(max_length=50)  # "grading", "solution", "answer"
    threshold_value = models.FloatField()
    
    # 변경 이력 (10K 대비 필수)
    changed_by = models.CharField(max_length=64)  # 사용자 ID
    changed_at = models.DateTimeField(auto_now=True)
    previous_value = models.FloatField(null=True)  # 이전 값 (롤백용)
    
    class Meta:
        unique_together = [("tenant_id", "subject_id", "teacher_id", "threshold_type")]
        indexes = [
            models.Index(fields=["tenant_id", "threshold_type"]),
        ]

class ThresholdChangeHistoryModel(BaseModel):
    """Threshold 변경 이력 (운영 추적)"""
    config = models.ForeignKey(ThresholdConfigModel, on_delete=models.CASCADE)
    old_value = models.FloatField()
    new_value = models.FloatField()
    changed_by = models.CharField(max_length=64)
    changed_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField(blank=True)  # 변경 사유
    
    class Meta:
        db_table = "threshold_change_history"
        indexes = [
            models.Index(fields=["changed_at"]),
            models.Index(fields=["changed_by"]),
        ]
```

**UI 표시 예시:**
```
현재 grading threshold: 0.55
(선생 설정값 적용됨, 변경일: 2026-02-10)
[변경 이력 보기] [롤백]
```

**운영 데이터 기반 튜닝:**

1. 초기: 기본값(0.6) 사용
2. 운영 데이터 수집: 실제 처리 결과와 조교 피드백 수집
3. 튜닝: 학원/과목/선생별로 threshold 조정
4. 모니터링: 조정 후 정확도 변화 추적

---

## 5. API 서버 연동 설계

### 4.1 정책 기반 성취도 계산

**위치:** API 서버 (AI 워커 아님)

**로직:**
```python
def calculate_achievement_score(analysis_result: dict, policy: dict) -> float:
    base_score = 1.0
    if not analysis_result.get("has_grading"):
        base_score -= policy.get("no_grading_penalty", 0.1)
    # ... 풀이, 답안 동일
    return max(0.0, base_score)
```

**원칙:** AI는 팩트(유무)만 전달, 비즈니스 로직(감점)은 API 서버 담당

### 4.2 서술형 답안지 이미지 제공

**워크플로우:**
1. AI 워커에서 추출된 이미지 URL 수신
2. 식별자 기반 학생 매칭
3. 프론트엔드에 이미지 URL 제공
4. 조교가 배점 입력

---

## 6. 구현 단계 주의사항 (실무 팁)

### 6.1 서술형 추출 시 패딩 처리

**문제:** 좌표값대로만 자르면 글씨 끝부분이 잘림

**해결:** 바운딩 박스 추출 시 **상하좌우 5~10% 패딩** 추가

```python
def extract_essay_region(image, bbox, padding_ratio=0.08):
    """
    bbox: [x, y, w, h]
    padding_ratio: 0.08 = 8% 패딩
    """
    x, y, w, h = bbox
    padding_x = int(w * padding_ratio)
    padding_y = int(h * padding_ratio)
    
    # 패딩 적용 (이미지 경계 체크)
    x_start = max(0, x - padding_x)
    y_start = max(0, y - padding_y)
    x_end = min(image.width, x + w + padding_x)
    y_end = min(image.height, y + h + padding_y)
    
    return image.crop((x_start, y_start, x_end, y_end))
```

### 6.2 동영상 처리 타임아웃 동적 조절

**문제:** 동영상 파일 용량이 크면 S3 다운로드 시간이 병목

**해결:** 파일 크기에 비례하여 타임아웃 동적 조절

```python
def calculate_timeout(file_size_mb: float, base_timeout: int = 60) -> int:
    """
    file_size_mb: 파일 크기 (MB)
    base_timeout: 기본 타임아웃 (초)
    Returns: 동적 타임아웃 (초)
    """
    # 100MB당 +30초
    additional_timeout = int((file_size_mb / 100) * 30)
    return base_timeout + additional_timeout
```

### 6.3 결과값에 Confidence 점수 포함

**문제:** boolean만 반환하면 "확신이 없는 경우만 조교에게 알림" 기능 구현 불가

**해결:** 모든 유무 판단 결과에 confidence 점수 포함

```python
# 출력 예시
{
    "has_grading": True,
    "grading_confidence": 0.85,  # 0.0 ~ 1.0
    "has_solution": True,
    "solution_confidence": 0.72,
    "has_answer": True,
    "answer_confidence": 0.91,
}

# 활용 예시: confidence < 0.7 인 경우만 조교에게 알림
if result.get("grading_confidence", 1.0) < 0.7:
    notify_teacher("채점 여부 판단에 확신이 없습니다. 확인 부탁드립니다.")
```

---

## 7. 구현 로드맵

### Phase 1: 필수 기능 (최우선) - 6-10일

1. ✅ **입력 품질 게이트** (Pre-Validation Layer)
   - 구현 난이도: 낮음
   - 예상 시간: 1-2일
   - **구현 전 필수**

2. ✅ **OMR 스캔 파일 자동 채점 완벽화**
   - A 케이스 필수
   - Basic에서 CPU 완벽 처리
   - 구현 난이도: 중
   - 예상 시간: 3-5일

3. ✅ **서술형 답안지 추출**
   - A 케이스 필수
   - 스캔 파일 기반
   - 구현 난이도: 중
   - 예상 시간: 3-5일

### Phase 2: 기능 강화 (단기) - 11-18일

4. ⚠️ **Dispatcher 모듈화**
   - 구현 난이도: 낮음
   - 예상 시간: 1-2일
   - **구현 전 필수**

5. ⚠️ **과제 사진 분석 강화** (다중 신호 기반)
   - B 케이스 필수
   - 유무 판단 정확도 중요
   - 구현 난이도: 중-높음
   - 예상 시간: 5-7일

6. ⚠️ **과제 동영상 분석 강화**
   - B 케이스 필수
   - 구현 난이도: 중-높음
   - 예상 시간: 5-7일

7. 🔄 **정책 기반 성취도 계산** (API 서버)
   - 구현 난이도: 낮음
   - 예상 시간: 1-2일

### Phase 3: 프리미엄 기능 (중기) - 12-17일

8. 🔄 **촬영물 OMR 인식** (`mode="photo"`/`video`)
   - C 케이스 선택적
   - 프리미엄(GPU)으로 분리
   - 구현 난이도: 높음
   - 예상 시간: 7-10일

9. 🔄 **동영상에서 OMR 이미지 추출**
   - C 케이스 선택적
   - 구현 난이도: 중-높음
   - 예상 시간: 5-7일

**총 예상 기간:** Phase 1-2 완료 시 기본 기능 완성 (약 17-28일)

---

## 5. 10K 환경 대비 스케일링 전략

### 5.1 Job Type별 Queue 분리 (Head-of-Line Blocking 방지)

**문제:** 동영상 분석(수십 초)과 OMR 스캔(1~2초)이 같은 큐를 사용하면 동영상 작업이 큐를 점령하여 OMR 작업이 무한 대기

**해결:** Job Type별 전용 큐 분리

**SQS Queue 구조:**

```
Basic Tier:
├── omr_scan_queue          (최우선, 빠른 처리)
├── homework_photo_queue    (중간 우선순위)
├── homework_video_queue    (낮은 우선순위, 긴 처리 시간)
└── essay_extraction_queue  (OMR과 연계)

Premium Tier:
└── premium_gpu_queue       (모든 GPU 작업)
```

**구현 예시:**

```python
# apps/shared/contracts/ai_job.py

def get_queue_name(job_type: str, tier: str) -> str:
    """Job Type별 Queue 이름 반환"""
    if tier == "premium":
        return "ai-worker-premium-gpu-queue"
    
    # Basic: Job Type별 분리
    queue_map = {
        "omr_grading": "ai-worker-omr-scan-queue",
        "essay_answer_extraction": "ai-worker-essay-queue",
        "homework_photo_analysis": "ai-worker-homework-photo-queue",
        "homework_video_analysis": "ai-worker-homework-video-queue",
        "omr_video_extraction": "ai-worker-omr-video-queue",
    }
    return queue_map.get(job_type, "ai-worker-basic-default-queue")
```

**워커 배정 전략:**

- **OMR 전용 워커:** `omr_scan_queue`만 처리 (빠른 응답 보장)
- **Homework 전용 워커:** `homework_*` 큐 처리 (긴 처리 시간 허용)
- **범용 워커:** 모든 Basic 큐 폴링 (유연성)

### 5.2 Auto-Scaling 전략 (SQS 기반)

**문제:** 단순 CPU 사용률로 스케일링하면 큐가 쌓여도 워커가 늘어나지 않음

**해결:** SQS 메시지 체류 시간 기반 스케일링

**스케일링 지표:**

| 지표 | 임계값 | 액션 |
|------|--------|------|
| **ApproximateAgeOfOldestMessage** | > 30초 | Scale Out (+2 workers) |
| **ApproximateNumberOfMessages** | > 200 | Scale Out (+2 workers) |
| **ApproximateNumberOfMessages** | < 10 | Scale In (-1 worker) |
| **CPU 사용률** | > 80% | Scale Out (+1 worker) |
| **평균 처리 시간** | > 5초 (OMR) | Scale Out (+1 worker) |

**구현 예시 (CloudWatch Alarms):**

```python
# CloudWatch Alarms 설정

# OMR 큐 대기 시간 알람
alarm_omr_queue_age = {
    "MetricName": "ApproximateAgeOfOldestMessage",
    "Namespace": "AWS/SQS",
    "QueueName": "ai-worker-omr-scan-queue",
    "Threshold": 30,  # 30초 초과 시
    "Action": "scale_out_omr_workers",
}

# Homework 큐 메시지 수 알람
alarm_homework_queue_length = {
    "MetricName": "ApproximateNumberOfMessages",
    "Namespace": "AWS/SQS",
    "QueueName": "ai-worker-homework-video-queue",
    "Threshold": 200,  # 200개 초과 시
    "Action": "scale_out_homework_workers",
}
```

**워커 웜업 대응:**

- **피크 시간대 스케줄링:** 학원 시험 종료 시간(예: 오후 5시)에 맞춰 워커 최소 개수 미리 증가
- **예측 스케일링:** 과거 데이터 기반 트래픽 예측하여 사전 스케일링

### 5.3 Lambda-based Pre-Validation (API 서버 보호)

**문제:** 1만 명이 동시 업로드 시 API 서버가 이미지 검증으로 먼저 뻗을 수 있음

**해결:** S3 Trigger → Lambda에서 검증 수행

**워크플로우:**

```
1. 파일 업로드 → S3
2. S3 Event Trigger → Lambda 함수 실행
3. Lambda에서 Pre-Validation 수행:
   - 해상도 체크
   - 왜곡 정도 체크
   - 파일 포맷 체크
4. 검증 결과 → DynamoDB/S3에 저장
5. API 서버는 검증 결과만 조회하여 AIJob 생성
```

**구현 예시:**

```python
# Lambda 함수: s3-validation-trigger

def lambda_handler(event, context):
    """S3 업로드 시 자동 검증"""
    s3_event = event['Records'][0]['s3']
    bucket = s3_event['bucket']['name']
    key = s3_event['object']['key']
    
    # S3에서 이미지 다운로드
    image = download_from_s3(bucket, key)
    
    # Pre-Validation 수행
    ok, error_msg = validate_input_for_basic(
        tier="basic",
        job_type=infer_job_type(key),
        image=image,
    )
    
    # 결과 저장 (DynamoDB)
    save_validation_result(
        file_key=key,
        is_valid=ok,
        error_message=error_msg,
        validated_at=datetime.now(),
    )
    
    return {"statusCode": 200}
```

### 5.4 Circuit Breaker (Short-circuiting)

**문제:** 다중 신호 점수화에서 첫 번째 신호가 명확한데도 나머지 무거운 알고리즘 실행

**해결:** Confidence가 높으면 나머지 알고리즘 건너뛰기

**구현 예시:**

```python
def analyze_homework_photo(image):
    """다중 신호 점수화 (Short-circuiting)"""
    
    # 1. 첫 번째 신호: 색상 분석 (가장 빠름)
    color_score = analyze_color(image)
    if color_score > 0.95:  # 매우 명확
        return {
            "has_grading": True,
            "grading_confidence": color_score,
            "short_circuited": True,  # 나머지 알고리즘 건너뛰기
        }
    
    # 2. 두 번째 신호: 컨투어 검출 (중간)
    contour_score = detect_contours(image)
    combined_score = (color_score * 0.4 + contour_score * 0.3)
    if combined_score > 0.90:  # 명확
        return {
            "has_grading": True,
            "grading_confidence": combined_score,
            "short_circuited": True,
        }
    
    # 3. 세 번째 신호: 클러스터 분석 (가장 무거움)
    cluster_score = analyze_clusters(image)
    final_score = (color_score * 0.4 + contour_score * 0.3 + cluster_score * 0.3)
    
    return {
        "has_grading": final_score > threshold,
        "grading_confidence": final_score,
        "short_circuited": False,
    }
```

### 5.5 Idempotency (중복 처리 방지)

**문제:** 10K 환경에서 동일 파일 재업로드, 네트워크 재시도 등으로 중복 요청 발생

**해결:** Idempotency Key 도입. **동시 요청 시 500 방지 필수:** create 후 IntegrityError 시 기존 Job 반환.

**구현 (안전한 create):**

```python
from django.db import IntegrityError

class AIJobModel(BaseModel):
    # ... 기존 필드
    
    idempotency_key = models.CharField(max_length=256, unique=True, null=True, blank=True)
    force_rerun = models.BooleanField(default=False)
    rerun_reason = models.TextField(blank=True, default="")
    
    class Meta:
        indexes = [
            models.Index(fields=["idempotency_key"]),
        ]

# API 서버에서 AIJob 생성 시 (동시 요청 시 500 방지)
def create_ai_job(job_type, payload, tenant_id, exam_id=None, student_id=None):
    """Idempotency Key로 중복 방지. 동시 요청 시 IntegrityError → 기존 Job 반환."""
    
    idempotency_key = generate_idempotency_key(...)
    
    try:
        job = AIJobModel.objects.create(
            job_id=...,
            job_type=job_type,
            payload=payload,
            idempotency_key=idempotency_key,
            ...
        )
    except IntegrityError:
        job = AIJobModel.objects.get(idempotency_key=idempotency_key)
    return job

def generate_idempotency_key(tenant_id, exam_id, student_id, job_type, file_hash):
    """컨텍스트 기반 Idempotency Key 생성"""
    # 같은 시험 + 같은 학생 + 같은 파일 = 중복
    key_parts = [
        tenant_id,
        exam_id or "none",
        student_id or "none",
        job_type,
        file_hash,
    ]
    return ":".join(key_parts)

# 재처리 경로 (force_rerun): 동일하게 IntegrityError 처리
# force_rerun 시 idempotency_key에 ":rerun:{job.id}" 등을 붙여 unique 유지 후 create,
# try/except IntegrityError → get(idempotency_key=...) 로 500 방지
def create_ai_job_with_rerun(job_type, payload, tenant_id, exam_id=None, student_id=None, force_rerun=False, rerun_reason=None):
    """Idempotency Key로 중복 방지 (재처리 경로 포함). 트랜잭션 충돌 시에도 500 없음."""
    
    idempotency_key = generate_idempotency_key(...)
    effective_key = f"{idempotency_key}:rerun:{uuid}" if force_rerun else idempotency_key
    
    try:
        job = AIJobModel.objects.create(..., idempotency_key=effective_key, force_rerun=force_rerun, ...)
    except IntegrityError:
        job = AIJobModel.objects.get(idempotency_key=effective_key)
    return job
```

**재처리 경로:**
- **기본:** Idempotency Key로 중복 방지 (영구)
- **예외:** `force_rerun=True` 플래그로 관리자 재처리 허용
- **사용 사례:** "이 건 다시 돌려주세요" CS 요청 시
- **구현:** 키에 `:rerun:{job_id}` 추가하여 unique 유지, try/except IntegrityError로 500 방지

**Idempotency Key 생성 규칙 (수정됨):**

- **기존 (위험):** `file_hash`만 사용 → 동일 이미지 다른 학생/시험에서 충돌
- **수정 (안전):** `tenant_id + exam_id + student_id + job_type + file_hash`
- **만료시간 제거:** 24시간 만료 방식 대신 컨텍스트 기반으로 영구 중복 방지

### 5.6 S3 최적화 전략

**문제:** 10K 환경에서 이미지 업로드/다운로드 비용 및 네트워크 지연

**해결:**

1. **이미지 압축:** 서술형 추출 이미지는 WebP 포맷 사용 (용량 70% 절감)
2. **로컬 캐싱:** 동일 이미지에 대해 여러 Job 발생 시 워커 내 로컬 캐시 활용
3. **S3 Hot Partition 방지:** 파일 저장 경로에 UUID 프리픽스 사용
4. **Multipart Streaming:** 대용량 동영상은 multipart streaming 처리
5. **파일 크기 제한:** 동영상은 일정 MB 이상 Basic에서 거부

**구현 예시:**

```python
# S3 경로 구조 (Hot Partition 방지)
def get_s3_key(tenant_id, file_hash):
    """UUID 프리픽스로 Hot Partition 방지"""
    prefix = str(uuid.uuid4())[:8]  # 랜덤 프리픽스
    return f"{prefix}/{tenant_id}/{file_hash[:2]}/{file_hash}"

# 이미지 압축
def compress_image(image, format="webp", quality=85):
    """WebP 포맷으로 압축 (용량 70% 절감)"""
    if format == "webp":
        return image.save(format="webp", quality=quality, optimize=True)
    return image

# 파일 크기 제한 (Pre-Validation)
def validate_file_size(file_size_mb, job_type):
    """Basic에서 파일 크기 제한"""
    limits = {
        "homework_video_analysis": 200,  # 200MB
        "omr_video_extraction": 100,     # 100MB
        "omr_grading": 50,               # 50MB
    }
    limit = limits.get(job_type, 50)
    if file_size_mb > limit:
        return False, f"File size exceeds {limit}MB limit for Basic tier"
    return True, None
```

### 5.7 GPU Fallback 비용 제어

**문제:** 자동 GPU Fallback이 무조건 발생하면 비용 폭발

**해결:** 관리자 설정 기반 제어

**Fallback 정책 (명시):**
> **Basic processing 실패도 Premium이면 Fallback 시도 (단, 비용 제어 조건 통과 시)**

**Fallback 트리거:**
1. **검증 실패:** VALIDATING 단계에서 실패 → FALLBACK_TO_GPU
2. **처리 실패:** PROCESSING 단계에서 실패 (재시도 불가) → FALLBACK_TO_GPU
   - 라이브러리 에러, 손상 파일, 시간초과 등 특정 에러 타입
   - Confidence 기반 Fallback도 가능

**구현:**

```python
class TenantConfigModel(BaseModel):
    """학원별 설정"""
    tenant_id = models.CharField(max_length=64, unique=True)
    
    # GPU Fallback 설정
    allow_gpu_fallback = models.BooleanField(default=False)  # 관리자 설정
    gpu_fallback_threshold = models.FloatField(default=0.5)  # Confidence 임계값
    
    # Premium 구독 여부
    has_premium_subscription = models.BooleanField(default=False)

# Fallback 로직 (검증 실패 + 처리 실패 모두 포함)
# ✅ error_type와 error_code 분리 (버그 수정: processing_failed 시 error_code로 비교)
def should_fallback_to_gpu(job, error_type=None, error_code=None, result=None):
    """
    GPU Fallback 여부 판단
    
    Fallback 트리거:
    1. 검증 실패 (error_type == "validation_failed") → 즉시 Fallback
    2. 처리 실패 (error_type == "processing_failed") 시:
       - result["confidence"] <= threshold → Fallback
       - error_code in ["library_error", "corrupted_file", "timeout"] → Fallback
    
    Premium + allow_gpu_fallback 설정을 반드시 통과해야 Fallback 허용.
    """
    tenant_config = TenantConfigModel.objects.get(tenant_id=job.tenant_id)
    
    if not tenant_config.has_premium_subscription:
        return False
    if not tenant_config.allow_gpu_fallback:
        return False
    
    if error_type == "validation_failed":
        return True
    
    if error_type == "processing_failed":
        if result is not None:
            confidence = result.get("confidence", 1.0)
            if confidence <= tenant_config.gpu_fallback_threshold:
                return True
        fallback_error_codes = ["library_error", "corrupted_file", "timeout", "low_quality"]
        if error_code is not None and error_code in fallback_error_codes:
            return True
    
    return False
```

---

## 8. 리스크 및 대응 방안 (10K 대비 보강)

| 리스크 | 영향도 | 대응 방안 |
|--------|--------|----------|
| **CPU 병목 (Head-of-Line Blocking)** | 🔥🔥🔥🔥🔥 | Job Type별 Queue 분리 (OMR 전용 큐), Auto-Scaling 전략 |
| **GPU Fallback 비용 폭발** | 🔥🔥🔥🔥 | 관리자 설정 기반 제어, Premium 구독자만 자동 Fallback |
| **DB JSON 비대화** | 🔥🔥🔥 | 핫/콜드 데이터 분리, 메트릭 테이블 분리, NoSQL 활용 |
| **Basic에서 CPU 실패 발생** | 높음 | Pre-Validation Layer (Lambda), 촬영물 거부 정책 |
| **유무 판단 정확도 부족** | 높음 | 다중 신호 기반 점수화 + Circuit Breaker + Threshold 튜닝 |
| **Dispatcher 비대화** | 중간 | 모듈화 구조로 분리 (Phase 2에서 적용) |
| **동영상 처리 성능 저하** | 중간 | Laplacian Variance 기반 프레임 선정, 키 프레임 활용, 타임아웃 동적 조절, 파일 크기 제한 |
| **프리미엄 기능 수요 증가** | 낮음 | GPU 워커 자동 전환, 비용 모니터링 |
| **CS 대응 불가** | 높음 | AI 결과 저장 전략 (Audit Trail), REVIEW_REQUIRED 상태 |
| **Threshold 운영 혼란** | 🔥🔥 | 변경 이력 테이블, UI 표시, 롤백 기능 |
| **S3 다운로드 병목** | 🔥🔥🔥 | Multipart Streaming, 파일 크기 제한, 로컬 캐싱 |
| **Confidence 알림 폭주** | 🔥🔥 | REVIEW_REQUIRED 상태, 검토 필요 큐 별도 운영 |
| **중복 처리** | 🔥🔥🔥 | Idempotency Key 도입, 파일 해시 기반 중복 방지 |
| **S3 Hot Partition** | 🔥🔥 | UUID 프리픽스 사용, 경로 분산 |
| **Worker 웜업 지연** | 🔥🔥 | 피크 시간대 스케줄링, 예측 스케일링 |
| **결과 집계 지연** | 🔥🔥 | 성취도 계산을 메시지 큐 기반 비동기 태스크로 처리 |

---

## 9. 기술 검토 요약

| 항목 | 검토 결과 | 비고 |
|------|-----------|------|
| **Job Type 확장** | ✅ 적절함 | `omr_video_extraction`, `homework_photo_analysis` 분리 좋음 |
| **Tier Enforcer** | ✅ 강력 추천 | Basic에서 "촬영물 거부" 정책은 CS 감소에 현명한 선택 |
| **API 계산 로직** | ✅ 합리적 | AI는 팩트(유무)만 전달, 비즈니스 로직(감점)은 API 서버 담당 → 유연 |
| **입력 품질 게이트** | ✅ 필수 | 운영 중 장애 방지, CS 감소 |
| **다중 신호 점수화** | ✅ 필수 | 실무 환경 변수(연필, 형광펜, 노이즈) 견디기 위해 |
| **Dispatcher 모듈화** | ✅ 권장 | 유지보수성, 테스트 용이성 향상 |
| **Job 상태 머신** | ✅ 필수 | 상태 추적, 재시도, GPU Fallback 로직 구현 필수 |
| **AI 결과 저장 전략** | ✅ 필수 | CS 대응, 재분석, 디버깅을 위한 Audit Trail |
| **Threshold 튜닝 전략** | ✅ 필수 | 학원/과목/선생별 정확도 향상을 위한 설정값 분리 |
| **Job Type별 Queue 분리** | ✅ 필수 (10K) | Head-of-Line Blocking 방지, OMR 우선 처리 보장 |
| **Auto-Scaling 전략** | ✅ 필수 (10K) | SQS 메시지 체류 시간 기반, 피크 트래픽 대응 |
| **Lambda Pre-Validation** | ✅ 권장 (10K) | API 서버 보호, 동시 업로드 대응 |
| **Circuit Breaker** | ✅ 권장 (10K) | 워커 회전율 향상, 불필요한 연산 방지 |
| **Idempotency** | ✅ 필수 (10K) | 중복 처리 방지, 재시도 안전성 |
| **S3 최적화** | ✅ 권장 (10K) | 비용 절감, 네트워크 지연 감소 |
| **GPU Fallback 제어** | ✅ 필수 (10K) | 비용 폭발 방지, 관리자 제어 |

### 9.1 실무 시나리오 A/B/C 반영 체크 및 프로덕션 보강 우선순위

**요구사항 반영 체크 (A/B/C + 요금제):**

| 시나리오 | 반영 여부 | 보강 포인트 |
|----------|-----------|--------------|
| **A. 스캔 OMR 시험 (CPU 완벽 처리)** | ✅ 반영 | OMR 전용 큐/워커 최소 2개, essay를 OMR 큐에 포함은 적절. **식별자 인식 실패/불확실** → 기존 **Submission.Status.NEEDS_IDENTIFICATION** + **manual_review.required** 사용 (본문 3.1.1 기존 구현 정합성 참고). |
| **B. 온라인 과제 제출 (유무판단 정확도)** | ✅ 반영 | "CPU 실패 없음" 정의 강화: (1) 사전 거부 가능 케이스는 **REJECTED_BAD_INPUT**, (2) 그 외는 **항상 SUCCESS** + 낮은 confidence는 REVIEW_CANDIDATE만 적재 (Shadow). 다중 신호 + **유무 판정 히스테리시스** + **동영상 top-k 투표** 보강 (본문 2.4, 3.4 반영). |
| **C. 비규격 답안지 촬영물 (Premium GPU)** | ✅ 반영 | Basic 촬영물 거부·Premium 분리 일치. **Premium에서는 GPU 실패도 사실상 금지**에 가깝게 설계 (재시도/프레임 재선정/가이드/최종 REVIEW 루트). |

**요금제/정책 로직 정합성:**  
- "CPU 기반 분석 실패가 시나리오에 있어선 안 됨" → Lite/Basic은 **FAIL 대신 거부 or 낮은 신뢰도 성공 처리**로 반영 (상태 머신·determine_status 정책 반영).

**프로덕션 보강 6개 (구현 우선순위):**

| # | 보강 항목 | 내용 | 우선순위 |
|---|-----------|------|----------|
| 1 | **상태 머신 운영 친화** | REJECTED_BAD_INPUT 추가. Lite/Basic: SUCCESS + review_candidate; REVIEW_REQUIRED는 Premium/조교 큐 전용. | P0 |
| 2 | **식별자 8자리 1급 시민** | 기존 필드 사용: Student **omr_code** (students/models.py), 워커 **identifier** / **confidence** / **status** (identifier.py). status in ("ambiguous","blank","error") → **NEEDS_IDENTIFICATION** + manual_review (ai_omr_result_mapper). 신규 필드 없이 문서·정책 정리. | P0 |
| 3 | **Queue 분리 (Phase 0)** | 현 구조 유지: omr_scan(+essay) 최소 2, homework_video 최소 1, basic_common 오토스케일. | 확정 |
| 4 | **Pre-Validation 거부 정책** | 거부 기준 운영 문장 고정, 거부 사유 프론트 노출 가능 (해상도/용량/흔들림/어두움/OMR 촬영물 Basic 금지 등). | P0 |
| 5 | **B 정확도 3종 세트** | 다중 신호 + **유무 판정 히스테리시스**(on_threshold/off_threshold) + **동영상 top-k(3~5) 투표**. | P1 |
| 6 | **Idempotency 키 규칙** | `tenant_id + exam_id + student_id + job_type + file_hash` 최종 확정. (이미 반영됨) | 확정 |

---

## 10. 단계별 적용 전략 (실무 가이드)

### 10.1 적용 시나리오

**현재 상황:**
- 첫 1개월: 대규모 트래픽 감당 불가 (소규모 시작)
- 3개월차부터: 10K 이상 감당 필요 (대규모 확장)

**핵심 원칙:**
- 초기: 최소 복잡도로 시작하되, 확장 가능한 구조 설계
- 중기: 실제 트래픽 증가에 맞춰 점진적 기능 추가
- 장기: 10K 대비 완전한 구조 적용

### 10.2 Phase 0: 초기 구축 (1개월) - 최소 구성

**목표:** 기본 기능 동작, 확장 가능한 구조만 구축

#### 적용할 기능 (필수)

| 기능 | 적용 수준 | 이유 |
|------|----------|------|
| **Job Type별 Queue** | 3개만 (혼합 모델) | OMR 전용 2개 + 범용 워커 |
| **기본 Auto-Scaling** | SQS 메시지 수 기반 | 단순하고 효과적 |
| **Idempotency Key** | `tenant_id + job_type + file_hash` | 중복 방지 필수 |
| **REVIEW_REQUIRED** | 이중 Threshold | 조교 과부하 방지 |
| **기본 Audit Trail** | RDB만 (JSONField 사용) | 단순성 유지 |
| **Pre-Validation** | API 서버에서 처리 | Lambda 비용/복잡도 회피 |

#### Queue 구조 (Phase 0)

**⚠️ 중요:** homework_video는 반드시 별도 큐로 분리 (영상 30초짜리 50개만 와도 다른 작업 지연)

```
Basic Tier:
├── omr_scan_queue          (OMR 전용 워커 2개 고정)
│   └── essay_extraction    (서술형 추출, OMR과 결합도 높음)
├── homework_video_queue    (동영상 전용, 긴 처리 시간)
└── basic_common_queue      (범용 워커, photo만)

Premium Tier:
└── premium_gpu_queue       (GPU 워커)
```

**워커 배정:**
- OMR 전용 워커 2개: `omr_scan_queue` + `essay_extraction` 처리 (시험 피크 시 OMR 채점 + 서술형 추출 함께 빠른 응답 보장)
- 동영상 전용 워커 1~2개: `homework_video_queue`만 처리 (긴 처리 시간 격리)
- 범용 워커 N개: `basic_common_queue` 처리 (photo만)

**⚠️ Essay 큐 위치 결정:**
- **추천:** OMR 큐에 포함 (시험 피크 시 "OMR 채점 + 서술형 추출"은 같이 빨라야 함)
- **대안:** basic_common에 포함 (단순하지만 시험 피크 시 지연 가능)
- **결정:** Phase 0에서는 OMR 큐에 포함 (조교 UX 우선)

#### Auto-Scaling 설정 (Phase 0)

**⚠️ 최소 워커 수 하한선 (워커 튐 방지):**

- **OMR 전용 워커:** 최소 **2개 고정** (scale-in 금지)
- **Video 전용 워커:** 최소 **1개 고정**
- **Common 워커만** scale-in 허용

시험 피크 패턴(0 → 300 → 10 → 0)에서 messages < 50으로 -1 하면 워커가 과도하게 줄어드는 현상 방지.

```python
# 보수적 설정 (과민 반응 방지) + 최소 워커 수
scaling_rules = {
    "scale_out": {
        "trigger": "ApproximateNumberOfMessages > 200",
        "action": "+2 workers",
        "cooldown": 600,  # 10분
    },
    "scale_in": {
        "trigger": "ApproximateNumberOfMessages < 50",
        "action": "-1 worker",
        "cooldown": 900,  # 15분
        "apply_to": "common_only",  # OMR/Video는 scale-in 금지
    },
}

# ASG 최소 용량 예시
asg_min_capacity = {
    "ai_worker_omr": 2,    # OMR 전용: 최소 2개
    "ai_worker_video": 1,  # Video 전용: 최소 1개
    "ai_worker_common": 0, # Common만 scale-in 허용
}
```

#### 데이터 저장 (Phase 0)

```python
# 모든 데이터 RDB에 저장 (단순성)
class AIResultModel(BaseModel):
    job = models.OneToOneField(AIJobModel, ...)
    payload = models.JSONField()  # 최종 결과
    analysis_metrics = models.JSONField(default=dict)  # 상세 메트릭
    confidence_scores = models.JSONField(default=dict)
    processing_time_seconds = models.FloatField(null=True)
    # ... 기타 필드
```

**보관 기간:** Phase 0 / Phase 1 동안 **최소 90일** 유지 (재채점, 학부모 분쟁, CS 대응, 데이터 분석). 30일 자동 Archive는 **Phase 2 이후**로만 적용.

#### Pre-Validation (Phase 0)

```python
# API 서버에서 처리 (Lightweight만)
def validate_input_for_basic(tier, job_type, payload):
    """1단계: 파일 크기, 헤더만 확인"""
    # S3 Range 요청으로 헤더만 읽기
    headers = s3_client.head_object(Bucket=bucket, Key=key)
    
    # 파일 크기 체크
    if headers['ContentLength'] > MAX_SIZE:
        return False, "File too large"
    
    # 헤더 정보로 포맷 확인
    content_type = headers.get('ContentType', '')
    if not is_allowed_format(content_type):
        return False, "Invalid format"
    
    return True, None

# 2단계: Worker에서 실제 검증 (Heavy)
# → Worker 내에서 이미지 다운로드 후 상세 검증
```

#### REVIEW_REQUIRED 전략 (Phase 0) — Lite/Basic은 "실패 없음"

**원칙:** Lite/Basic에서 **FAILED를 가능한 한 없앰.** B(과제 유무판단) 시나리오에서 confidence < threshold_low → FAILED는 CS/운영 이슈로 이어지므로, **거부 정책 대상만 REJECTED_BAD_INPUT**, 그 외는 **항상 SUCCESS**로 응답하고 confidence 낮으면 **REVIEW_CANDIDATE**로만 적재 (Shadow로 시작).

**⚠️ 위험:** Threshold 튜닝이 늦으면 REVIEW_REQUIRED가 10~15% 나올 수 있음 → 조교 과부하.

**해결:** Phase 0에서는 Shadow Mode로 운영. Shadow Mode는 DB/Redis 기반 런타임 설정 (상수 금지).

```python
# Lite/Basic: FAIL 대신 "거부 or 낮은 신뢰도 성공 처리"
# - tier in ("lite", "basic") → 애매해도 SUCCESS + flags.review_candidate=true (REVIEW_REQUIRED 아님)
# - Premium만 confidence 구간에 따라 REVIEW_REQUIRED 노출
from apps.domains.ai.services.runtime_flags import get_runtime_flag

def determine_status(confidence, threshold_low=0.5, threshold_high=0.8, tier="basic"):
    shadow_mode = get_runtime_flag("ai_shadow_mode", default=True)
    
    if tier in ("lite", "basic"):
        # Lite/Basic: 실패 없음. 낮은 confidence도 SUCCESS + 후보 플래그만
        if confidence < threshold_low:
            # 명확히 없음이어도 "실패" 대신 SUCCESS + review_candidate (운영 정책)
            return "SUCCESS", {"review_candidate": True, "confidence": confidence}
        elif threshold_low <= confidence < threshold_high:
            log_review_candidate(job_id, confidence)
            return "SUCCESS", {"review_candidate": True, "confidence": confidence}
        else:
            return "SUCCESS", {"review_candidate": False, "confidence": confidence}
    
    # Premium: REVIEW_REQUIRED 노출 가능
    if confidence < threshold_low:
        return "FAILED", {}
    elif threshold_low <= confidence < threshold_high:
        if shadow_mode:
            log_review_candidate(job_id, confidence)
            return "SUCCESS", {"review_candidate": True}
        return "REVIEW_REQUIRED", {}
    return "SUCCESS", {"review_candidate": False}

# Shadow Mode 히스테리시스 (재진입 방지)
def should_enable_review():
    """Shadow Mode 해제 조건 (히스테리시스)"""
    review_rate_7days = get_review_rate_last_7days()
    review_rate_24h = get_review_rate_last_24h()
    
    # Enable: 3% 이하 7일 연속
    if review_rate_7days <= 0.03:
        return True
    
    return False

def should_disable_review():
    """Shadow Mode 활성화 조건 (히스테리시스)"""
    review_rate_24h = get_review_rate_last_24h()
    
    # Disable (= Shadow로 복귀): 7% 이상 24시간 지속
    if review_rate_24h >= 0.07:
        return True
    
    return False
```

**운영 전략 (히스테리시스):**
- **Enable (Shadow 해제):** REVIEW 비율 3% 이하 7일 연속 → 조교 검토 활성화
- **Disable (Shadow 활성화):** REVIEW 비율 7% 이상 24시간 지속 → Shadow Mode로 복귀
- **목표:** REVIEW 비율 3~5% 유지
- **이유:** 5% 기준으로 on/off가 왔다갔다 하는 것을 방지 (운영 안정성)

#### 기본 메트릭 로깅 (Phase 0 필수)

**⚠️ 위험:** Phase 0은 문제가 가장 많이 발생하지만 분석 도구가 가장 약함

**해결:** 최소한의 메트릭 로깅은 필수

```python
# Prometheus/CloudWatch 메트릭 수집 (정의 통일)
metrics_to_collect = {
    # Job Type별 평균 처리 시간
    "job_processing_time": {
        "labels": ["job_type", "tier"],
        "type": "histogram",
    },
    
    # Queue별 대기 시간
    "queue_wait_time": {
        "labels": ["queue_name"],
        "type": "histogram",
    },
    
    # REVIEW_REQUIRED 비율
    "review_required_rate": {
        "labels": ["job_type"],
        "type": "gauge",
    },
    
    # FAIL 비율
    "fail_rate": {
        "labels": ["job_type", "error_type"],
        "type": "gauge",
    },
}

# 메트릭 정의 (Phase 0부터 통일, 변경 금지)
METRIC_DEFINITIONS = {
    "queue_wait_time": {
        "definition": "now - message.SentTimestamp",
        "unit": "seconds",
        "description": "SQS 메시지가 큐에 대기한 시간",
    },
    
    "processing_time": {
        "definition": "handler 시작 ~ 끝 (S3 다운로드 시간 포함)",
        "unit": "seconds",
        "description": "워커에서 실제 처리 시간 (S3 다운로드 포함)",
        "note": "S3 다운로드 시간 포함 여부를 명시적으로 정의",
    },
    
    "review_rate": {
        "definition": "(review 후보 건수) / (전체 처리 건수) * 100",
        "unit": "percent",
        "description": "REVIEW_REQUIRED 상태가 될 후보 비율",
        "note": "Shadow Mode 포함 여부를 명시 (Shadow Mode에서는 실제 REVIEW_REQUIRED가 아니지만 후보로 카운트)",
        "shadow_mode_included": True,  # Shadow Mode 후보도 카운트
    },
}

# CloudWatch Custom Metrics
def emit_metrics(job_type, processing_time, queue_wait_time, status):
    cloudwatch.put_metric_data(
        Namespace="AIWorker",
        MetricData=[
            {
                "MetricName": "ProcessingTime",
                "Dimensions": [{"Name": "JobType", "Value": job_type}],
                "Value": processing_time,
                "Unit": "Seconds",
            },
            {
                "MetricName": "QueueWaitTime",
                "Value": queue_wait_time,
                "Unit": "Seconds",
            },
            {
                "MetricName": "JobStatus",
                "Dimensions": [
                    {"Name": "JobType", "Value": job_type},
                    {"Name": "Status", "Value": status},
                ],
                "Value": 1,
                "Unit": "Count",
            },
        ],
    )
```

**필수 메트릭:**
- Job Type별 평균 처리 시간
- Queue별 대기 시간
- REVIEW_REQUIRED 비율
- FAIL 비율

**이유:** Phase 1로 넘어갈 근거 데이터 확보 필수

#### 보류할 기능 (Phase 0)

- ❌ Lambda Pre-Validation (API 서버에서 처리)
- ❌ NoSQL 분리 (RDB만 사용)
- ❌ Circuit Breaker (전체 알고리즘 실행)
- ❌ Hot/Cold 자동 분리 (90일 후 수동 Archive)
- ❌ 복잡한 GPU Fallback 조건 (Premium 구독 여부만)
- ❌ Weighted Priority Polling (단순 폴링)

**이유:** 복잡도 최소화, 운영 부담 감소

### 10.3 Phase 1: 점진적 확장 (2개월) - 운영 안정화

**목표:** 실제 트래픽 증가에 맞춰 기능 추가, 운영 효율성 향상

#### 추가할 기능

| 기능 | 적용 시점 | 이유 |
|------|----------|------|
| **우선순위 큐 폴링** | 트래픽 증가 시 | 리소스 효율성 향상 (단순 모델) |
| **메트릭 테이블 분리** | DB 부하 발생 시 | 성능 최적화 |
| **Lambda Lightweight Pre-Validation** | API 서버 부하 증가 시 | 서버 보호 |
| **Threshold Auto-Tuning** | 운영 데이터 축적 시 | 정확도 향상 |
| **DLQ 모니터링** | 실패 패턴 발견 시 | 문제 추적 |

#### 우선순위 큐 폴링 구현 (단순 모델)

**⚠️ 위험:** Weighted random polling은 실제 큐 길이를 고려하지 않아 비효율적

**해결:** 단순 우선순위 큐 방식 (비어있으면 다음 큐)

```python
# 범용 워커가 여러 큐를 우선순위로 폴링
def priority_poll_queues(worker_id):
    """우선순위 기반 큐 폴링 (단순 모델)"""
    queues = [
        "omr_scan_queue",        # 최우선
        "homework_photo_queue",  # 두 번째
        "essay_extraction_queue", # 세 번째
    ]
    
    # 우선순위대로 순차 확인, 비어있으면 다음 큐
    for queue_name in queues:
        message = sqs_client.receive_message(
            QueueUrl=queue_name,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=0,  # 즉시 반환 (비어있으면 다음 큐)
        )
        if message:
            return message, queue_name
    
    return None, None
```

**장점:**
- 실제 큐 상태 기반 폴링 (비어있으면 즉시 다음 큐)
- SQS 비용 절감 (불필요한 폴링 감소)
- 구현 단순, 안정적

#### 메트릭 테이블 분리

```python
# Phase 1에서 추가
class AIJobMetricsModel(BaseModel):
    """메트릭 별도 테이블 (인덱스 최적화)"""
    job = models.OneToOneField(AIJobModel, related_name="metrics")
    
    # 정규화된 컬럼 (JSONField 대신)
    grading_confidence = models.FloatField(null=True, db_index=True)
    solution_confidence = models.FloatField(null=True, db_index=True)
    answer_confidence = models.FloatField(null=True, db_index=True)
    processing_time_seconds = models.FloatField(null=True, db_index=True)
    
    class Meta:
        db_table = "ai_job_metrics"
```

#### Lambda Lightweight Pre-Validation

```python
# S3 Trigger → Lambda (헤더만 확인)
def lambda_handler(event, context):
    """Lightweight Pre-Validation"""
    s3_event = event['Records'][0]['s3']
    bucket = s3_event['bucket']['name']
    key = s3_event['object']['key']
    
    # S3 HeadObject로 메타데이터만 확인
    response = s3_client.head_object(Bucket=bucket, Key=key)
    
    # 파일 크기 체크
    if response['ContentLength'] > MAX_SIZE:
        save_validation_result(key, False, "File too large")
        return
    
    # Content-Type 체크
    content_type = response.get('ContentType', '')
    if not is_allowed_format(content_type):
        save_validation_result(key, False, "Invalid format")
        return
    
    # 이미지의 경우 Range 요청으로 헤더만 읽기
    if is_image(content_type):
        headers = s3_client.get_object(
            Bucket=bucket,
            Key=key,
            Range='bytes=0-1023'  # 첫 1KB만
        )
        # 헤더에서 해상도 추출 (경량 라이브러리)
        resolution = extract_resolution_from_headers(headers['Body'].read())
        if resolution[0] < 600:  # 최소 해상도
            save_validation_result(key, False, "Resolution too low")
            return
    
    save_validation_result(key, True, None)
```

#### DLQ 모니터링

```python
# 모든 큐에 DLQ 설정
dlq_config = {
    "omr_scan_queue": {
        "dlq_name": "omr_scan_dlq",
        "max_receive_count": 3,
        "alarm_threshold": 10,  # DLQ에 10개 이상 쌓이면 알림
    },
    # ... 다른 큐들
}

# CloudWatch Alarm → Slack 알림
def check_dlq():
    """DLQ 모니터링"""
    for queue_name, config in dlq_config.items():
        dlq_messages = get_dlq_message_count(config["dlq_name"])
        if dlq_messages >= config["alarm_threshold"]:
            send_slack_alert(
                f"⚠️ DLQ Alert: {queue_name} has {dlq_messages} failed messages"
            )
```

### 10.4 Phase 2: 10K 대비 완전 구조 (3개월+) - 대규모 확장

**목표:** 10K 규모 완전 대비, 모든 고급 기능 적용

#### 추가할 기능 (완전 구조)

| 기능 | 적용 수준 | 이유 |
|------|----------|------|
| **Queue 완전 분리** | 5~6개 큐 | Head-of-Line Blocking 완전 방지 |
| **고급 Auto-Scaling** | SQS Age 기반 | 정밀한 스케일링 |
| **Lambda Heavy Pre-Validation** | 전체 검증 | API 서버 완전 보호 |
| **Circuit Breaker** | Short-circuiting | 워커 회전율 향상 |
| **Hot/Cold 데이터 분리** | 자동 Archive | 비용 최적화 |
| **NoSQL 분리** | DynamoDB/MongoDB | 성능 최적화 (정량 조건 충족 시) |
| **복잡한 GPU Fallback** | 다중 조건 | 비용 제어 |

#### Queue 완전 분리

```
Basic Tier:
├── omr_scan_queue          (OMR 전용)
├── essay_extraction_queue   (서술형 추출)
├── homework_photo_queue    (사진 분석)
└── homework_video_queue    (동영상 분석)

Premium Tier:
└── premium_gpu_queue        (모든 GPU 작업)
```

#### 고급 Auto-Scaling

```python
# SQS Age 기반 정밀 스케일링
scaling_rules = {
    "omr_scan_queue": {
        "aggressive": True,  # 빠른 응답 필요
        "age_threshold": 10,  # 10초 초과 시 즉시 Scale Out
        "message_threshold": 100,
    },
    "homework_video_queue": {
        "aggressive": False,  # 완만한 스케일링
        "age_threshold": 60,  # 60초 초과 시 Scale Out
        "message_threshold": 200,
    },
}
```

#### NoSQL 도입 타이밍 (정량 조건)

**⚠️ 위험:** 너무 빨리 도입하면 데이터 일관성 이슈, CS 분석 복잡도 증가

**해결:** 정량 조건 명시

```python
# NoSQL 도입 조건
def should_introduce_nosql():
    """정량 조건 충족 시에만 NoSQL 도입"""
    conditions = [
        # RDB CPU 부하
        get_rdb_cpu_avg_last_7days() > 70,
        
        # 또는 메트릭 테이블 크기
        get_metrics_table_row_count() > 10_000_000,  # 1천만 row 초과
    ]
    
    return any(conditions)

# 도입 전 체크리스트
nosql_checklist = [
    "RDB CPU > 70% 평균 지속 7일 이상",
    "또는 AIJobMetrics 테이블 1천만 row 초과",
    "데이터 일관성 전략 수립",
    "CS 분석 프로세스 정의 (RDB + NoSQL + S3)",
]
```

**도입 조건:**
- RDB CPU > 70% 평균 지속 7일 이상
- 또는 AIJobMetrics 테이블 1천만 row 초과

**도입 전 필수:**
- 데이터 일관성 전략 수립
- CS 분석 프로세스 정의 (RDB + NoSQL + S3 통합 조회)

#### Hot/Cold 데이터 자동 분리 (Phase 2 이후)

**Phase 0/1:** 보관 기간 90일 유지. 자동 Archive 미적용.

**Phase 2 이후:** 30일 기준 자동 Archive 적용.

```python
# 30일 기준 자동 Archive (Phase 2 이후만 사용)
def archive_old_results():
    """30일 이상 된 데이터를 S3 Archive로 이동 (Phase 2 전환 후 활성화)"""
    cutoff_date = timezone.now() - timedelta(days=30)
    
    old_results = AIResultModel.objects.filter(
        created_at__lt=cutoff_date,
        archived=False,
    )
    
    for result in old_results:
        # 상세 메트릭을 S3 JSON으로 저장
        archive_key = f"archive/{result.job_id}/metrics.json"
        s3_client.put_object(
            Bucket=ARCHIVE_BUCKET,
            Key=archive_key,
            Body=json.dumps(result.analysis_metrics),
        )
        
        # RDB에서는 최소한의 데이터만 유지
        result.analysis_metrics = {}  # 비우기
        result.archived = True
        result.archive_url = f"s3://{ARCHIVE_BUCKET}/{archive_key}"
        result.save()
```

### 10.5 단계별 체크리스트

#### Phase 0 체크리스트 (1개월)

- [ ] Queue 4개 구축 (OMR+Essay 전용 + 동영상 전용 + 범용)
- [ ] 기본 메트릭 로깅 (Prometheus/CloudWatch)
- [ ] 기본 Auto-Scaling 설정 (Cooldown 필수)
- [ ] Idempotency Key 구현 (`tenant_id + exam_id + student_id + job_type + file_hash`)
- [ ] REVIEW_REQUIRED 이중 Threshold 구현 (Shadow Mode 히스테리시스)
- [ ] 기본 Audit Trail (RDB JSONField)
- [ ] API 서버 Pre-Validation (Lightweight)
- [ ] DLQ 기본 설정

#### Phase 1 체크리스트 (2개월)

- [ ] 우선순위 큐 폴링 구현 (단순 모델)
- [ ] 메트릭 테이블 분리 (`AIJobMetricsModel`)
- [ ] Lambda Lightweight Pre-Validation
- [ ] Threshold Auto-Tuning 로직
- [ ] DLQ 모니터링 및 알림
- [ ] 운영 대시보드 구축

#### Phase 2 체크리스트 (3개월+)

- [ ] Queue 완전 분리 (5~6개)
- [ ] 고급 Auto-Scaling (SQS Age 기반)
- [ ] Lambda Heavy Pre-Validation
- [ ] Circuit Breaker 구현
- [ ] Hot/Cold 자동 분리 (30일 기준, **Phase 2 이후**)
- [ ] NoSQL 도입 (DynamoDB/MongoDB)
- [ ] 복잡한 GPU Fallback 조건

### 10.6 마이그레이션 가이드

#### Phase 0 → Phase 1 마이그레이션

1. **메트릭 테이블 분리:**
   ```python
   # 기존 JSONField 데이터를 정규화된 테이블로 마이그레이션
   def migrate_metrics_to_table():
       results = AIResultModel.objects.filter(analysis_metrics__isnull=False)
       for result in results:
           metrics = result.analysis_metrics
           AIJobMetricsModel.objects.create(
               job=result.job,
               grading_confidence=metrics.get('grading_confidence'),
               # ... 기타 필드
           )
   ```

2. **Weighted Priority Polling 적용:**
   - 기존 범용 워커에 Weighted Polling 로직 추가
   - 점진적 롤아웃 (50% → 100%)

#### Phase 1 → Phase 2 마이그레이션

1. **Queue 분리:**
   - 기존 `basic_common_queue`를 3개로 분리
   - 기존 메시지는 그대로 처리, 새 메시지만 분리된 큐로

2. **Hot/Cold 분리:**
   - 기존 데이터는 그대로 유지
   - Phase 2 전환 후 새 데이터부터 30일 기준 Archive 적용 (Phase 0/1은 90일 보관)

### 10.7 Phase 전환 기준 (트래픽 지표 기반)

**⚠️ 중요:** 시간 기준이 아닌 트래픽 지표 기반으로 전환

#### Phase 0 → Phase 1 전환 조건

```python
def should_move_to_phase1():
    """트래픽 지표 기반 Phase 전환 (필수 + 보조 조건)"""
    # 필수 조건: 사용자 체감 지표 (큐 지연)
    required_condition = get_avg_queue_wait_time_last_7days() > 10  # 10초 초과
    
    # 보조 조건: 1개 이상 충족
    auxiliary_conditions = [
        get_daily_avg_jobs_last_7days() >= 5000,  # 일 평균 Job 5,000건 이상
        get_review_required_rate_last_7days() > 0.05,  # REVIEW_REQUIRED 비율 5% 초과
    ]
    
    # 필수 1개 + 보조 1개 이상
    return required_condition and any(auxiliary_conditions)
```

**전환 조건 (필수 + 보조):**
- **필수:** 평균 대기 시간 > 10초 (사용자 체감 지표)
- **보조 (1개 이상):**
  - 일 평균 Job 5,000건 이상 (최근 7일 평균)
  - 또는 REVIEW_REQUIRED 비율 > 5%

#### Phase 1 → Phase 2 전환 조건

```python
def should_move_to_phase2():
    """대규모 확장 필요 시 Phase 2 전환 (필수 + 보조 조건)"""
    # 필수 조건: 사용자 체감 지표 (큐 지연)
    required_condition = get_max_queue_age_last_7days() > 30  # 30초 초과 피크
    
    # 보조 조건: 1개 이상 충족
    auxiliary_conditions = [
        get_daily_avg_jobs_last_7days() >= 20000,  # 일 평균 Job 20,000건 이상
        get_rdb_cpu_avg_last_7days() > 60,  # RDB CPU 60% 초과
    ]
    
    # 필수 1개 + 보조 1개 이상
    return required_condition and any(auxiliary_conditions)
```

**전환 조건 (필수 + 보조):**
- **필수:** Queue Age > 30초 피크 발생 (사용자 체감 지표)
- **보조 (1개 이상):**
  - 일 평균 Job 20,000건 이상 (최근 7일 평균)
  - 또는 RDB CPU > 60% (최근 7일 평균)

**핵심:** 
- 시간이 아닌 실제 트래픽 지표로 전환 시점 결정
- **필수 조건 (사용자 체감) + 보조 조건 (1개 이상)** 구조로 전환 지연 방지
- all() 방식은 너무 엄격하여 한 조건이 안 맞아도 전환이 늦어질 수 있음

### 10.8 운영 복잡도 관리

**Phase 0 (단순):**
- Queue: 4개 (OMR 전용 + 동영상 전용 + 범용)
- 워커 타입: 3종 (OMR 전용 + 동영상 전용 + 범용)
- 데이터 저장: RDB만
- 검증: API 서버
- 메트릭: 기본 로깅 필수

**Phase 1 (중간):**
- Queue: 4개 (우선순위 폴링)
- 워커 타입: 3종
- 데이터 저장: RDB + 메트릭 테이블 분리
- 검증: API 서버 + Lambda Lightweight
- 메트릭: 상세 로깅

**Phase 2 (복잡):**
- Queue: 5~6개
- 워커 타입: 5~6종 (Queue별 전용)
- 데이터 저장: RDB + NoSQL (조건 충족 시) + S3 Archive
- 검증: Lambda Heavy

**핵심:** 단계적으로 복잡도 증가, 각 단계에서 운영 안정화 후 다음 단계 진행

---

## 11. 결론 및 다음 단계

### 결론

현재 설계는 **실무 요구사항을 충족할 수 있는 구조**로 평가된다. Tier 시스템, Job Type 분리, CPU/GPU 전략이 모두 적절하며, **단계별 적용 전략**을 통해 초기에는 최소 복잡도로 시작하되, 3개월차부터 10K 규모까지 대응 가능한 구조로 확장할 수 있다.

**단계별 적용의 핵심:**
- **Phase 0 (1개월):** 최소 구성으로 시작, 확장 가능한 구조만 구축
- **Phase 1 (2개월):** 실제 트래픽 증가에 맞춰 점진적 기능 추가
- **Phase 2 (3개월+):** 10K 대비 완전 구조 적용

이를 통해 **운영 복잡도를 관리하면서도 확장성을 확보**할 수 있다.

### 핵심 원칙 (재확인)

1. **라이트/베이직 플랜:** CPU 워커에서 완벽히 처리되어야 함. CPU 기반 분석 실패는 시나리오에 있어선 안 됨.
2. **프리미엄 플랜:** GPU 워커 자동 전환 가능, 고급 기능 제공
3. **기능 경량화 우선:** CPU에서 처리 가능하도록 알고리즘 최적화
4. **프리미엄 기능 격상:** CPU 처리 불가능한 고급 기능은 프리미엄(GPU)으로 분리

### 구현 전 필수 체크리스트

**기능 설계:**
- [ ] **입력 품질 게이트** 구현 (Pre-Validation Layer)
- [ ] **Dispatcher 모듈화** 구조 적용
- [ ] **다중 신호 점수화** 알고리즘 설계 (과제 분석)

**운영 설계 (프로덕션 완성형):**
- [ ] **Job 상태 머신** 정의 및 구현 (REVIEW_REQUIRED 포함)
- [ ] **AI 결과 저장 전략** 구현 (핫/콜드 분리, 메트릭 테이블 분리)
- [ ] **Threshold 튜닝 전략** 구현 (변경 이력, UI 표시, 롤백 기능)

**10K 대비 스케일링:**
- [ ] **Job Type별 Queue 분리** (OMR 전용 큐, Homework 전용 큐)
- [ ] **Auto-Scaling 전략** (SQS 메시지 체류 시간 기반)
- [ ] **Lambda-based Pre-Validation** (API 서버 보호)
- [ ] **Circuit Breaker** (Short-circuiting 로직)
- [ ] **Idempotency Key** 도입 (중복 처리 방지)
- [ ] **S3 최적화** (이미지 압축, Hot Partition 방지, 파일 크기 제한)
- [ ] **GPU Fallback 비용 제어** (관리자 설정 기반)
- [ ] **결과 집계 비동기화** (성취도 계산을 메시지 큐로)

**구현 팁:**
- [ ] 서술형 추출 시 패딩 처리 (5~10%)
- [ ] 동영상 처리 타임아웃 동적 조절
- [ ] 결과값에 confidence 점수 포함

### 다음 단계 (단계별 적용)

**Phase 0 (트래픽 지표 기준): 최소 구성**
1. **즉시 시작:** Phase 0 체크리스트 완료
   - Queue 4개 구축 (OMR 전용 + 동영상 전용 + 범용)
   - 기본 Auto-Scaling 설정 (Cooldown 필수)
   - 기본 메트릭 로깅 (Prometheus/CloudWatch) **필수**
   - Idempotency Key 구현 (컨텍스트 기반)
   - REVIEW_REQUIRED 이중 Threshold (Shadow Mode로 시작)
   - 기본 Audit Trail
   
2. **Phase 0 → Phase 1 전환 조건 확인 (필수 + 보조):**
   - **필수:** 평균 대기 시간 > 10초
   - **보조 (1개 이상):** 일 평균 Job 5,000건 이상 또는 REVIEW_REQUIRED 비율 > 5%

**Phase 1 (트래픽 지표 기준): 점진적 확장**
2. **운영 안정화 후:** Phase 1 체크리스트 완료
   - 우선순위 큐 폴링 (단순 모델)
   - 메트릭 테이블 분리
   - Lambda Lightweight Pre-Validation
   - Threshold Auto-Tuning
   - DLQ 모니터링
   
3. **Phase 1 → Phase 2 전환 조건 확인 (필수 + 보조):**
   - **필수:** Queue Age > 30초 피크 발생
   - **보조 (1개 이상):** 일 평균 Job 20,000건 이상 또는 RDB CPU > 60%

**Phase 2 (트래픽 지표 기준): 10K 대비 완전 구조**
3. **대규모 확장:** Phase 2 체크리스트 완료
   - Queue 완전 분리 (5~6개)
   - 고급 Auto-Scaling (SQS Age 기반)
   - Lambda Heavy Pre-Validation
   - Hot/Cold 자동 분리
   - NoSQL 도입 (정량 조건 충족 시)
   - Circuit Breaker

**핵심 원칙:** 
- 각 단계에서 운영 안정화 확인 후 다음 단계 진행
- **시간이 아닌 트래픽 지표 기반으로 Phase 전환**
- Phase 0에서 기본 메트릭 로깅은 필수 (문제 분석 근거 확보)

---

## 부록: 참고 문서

- 상세 설계: `docs/AI_WORKER_REAL_WORLD_DESIGN.md`
- Tier Enforcer: `apps/worker/ai_worker/ai/pipelines/tier_enforcer.py`
- Dispatcher: `apps/worker/ai_worker/ai/pipelines/dispatcher.py`
- AIJob Contract: `apps/shared/contracts/ai_job.py`

---

**문서 승인:** ✅  
**다음 리뷰:** Phase 1 완료 후
