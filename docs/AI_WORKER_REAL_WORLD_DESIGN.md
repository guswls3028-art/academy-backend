# AI 워커 실무 시나리오 기반 설계 제안서

## 📋 목차
1. [실무 시나리오 분석](#실무-시나리오-분석)
2. [현재 구조 분석](#현재-구조-분석)
3. [설계 제안](#설계-제안)
4. [구현 우선순위](#구현-우선순위)
5. [피드백 및 개선사항](#피드백-및-개선사항)
6. [구조 보강 (실무 리뷰 반영)](#6-구조-보강-실무-리뷰-반영)

---

## 실무 시나리오 분석

### A. 일반 시험 (OMR 스캔)
**워크플로우:**
1. 학생들이 학원에 옴
2. 학생들이 과제(숙제) 제출 → 조교가 수기로 검사 후 프로그램에 입력
3. 직후 바로 학원에서 OMR 답안지로 시험을 봄
4. 학생들이 제출한 OMR 답안지를 스캔해서 프로그램에 업로드
5. **AI CPU 워커**가 자동 채점 및 식별자 8자리 통해서 각 학생들에게 매칭해서 자동으로 서술형 점수 반영
6. 조교가 서술형 채점 후 점수 입력
7. 프로그램 내에서 동작 시 학생들이 실제로 서술형 답안지에 입력한 답이 이미지로 제공됨. 조교가 보고 프로그램에서 배점 가능하게

**핵심 요구사항:**
- ✅ OMR 자동 채점 (이미 구현됨: `omr_grading`)
- ✅ 식별자 8자리 인식 및 학생 매칭 (이미 구현됨: `identifier` detection)
- ✅ 서술형 답안지 이미지 추출 및 제공 (추가 구현 필요)

### B. 학생이 온라인 제출 시
**워크플로우:**
1. 학생이 과제를 미제출 했을 때 (학원에 안 가져옴) 집에서 제출할 수도 있음
2. 직접 자기가 푼 과제물을 사진 촬영 혹은 동영상으로 촬영해서 제출함
3. **CPU 워커**가 인식해서:
   - 1. 채점 여부 (유무 판단)
   - 2. 풀이 여부 (유무 판단)
   - 3. 답안 작성 여부 (유무 판단)
   - 선생이 선택한 정책에 따라 (ex) 채점 없을 시 10% 감점 등...) 과제 성취도를 제공함
   - + 풀이가 없고 미풀이 문항은 따로 추출됨
4. CPU 워커로 숙제 검사 시 고기능 AI는 사용되지 않음. **논리적 사고는 필요 없음. 단순히 숙제를 제대로 해왔는가 유무 판단만. 유무 판단은 정확해야 함.**

**핵심 요구사항:**
- ✅ 사진/동영상 처리 (이미 구현됨: `homework_video_analysis`)
- ⚠️ **채점 여부 감지** (유무 판단, 정확도 중요)
- ⚠️ **풀이 여부 감지** (유무 판단, 정확도 중요)
- ⚠️ **답안 작성 여부 감지** (유무 판단, 정확도 중요)
- ⚠️ **정책 기반 성취도 계산** (API 서버에서 처리 필요)
- ⚠️ **미풀이 문항 추출** (추가 구현 필요)

### C. 규격에 맞지 않는 답안지로 시험을 볼 때
**워크플로우:**
1. 시험 OMR을 실제 학교나 모의고사 대비로 A4 크기가 아니어서 스캔 파일이 불가할 때
2. 조교가 사진 혹은 동영상으로 학생 답안지 촬영
3. 프로그램에 업로드
4. 마찬가지로 OMR 인식해서 자동 채점 후 프로그램에 입력
5. 서술형은 이미지 파일로 제공 (A 유형과 동일)
6. 결과적으로 A와 동일하지만, 스캔 파일 제공이 안 되는 경우 동영상 or 사진 촬영물로 대체될 뿐임

**핵심 요구사항:**
- ✅ 비규격 답안지 처리 (이미 구현됨: `omr_grading` with `mode="photo"` or `mode="auto"`)
- ✅ 동영상에서 프레임 추출 (추가 구현 필요: 동영상 → 이미지 변환)
- ✅ 서술형 답안지 이미지 추출 (A와 동일, 추가 구현 필요)

**전략:**
- **우선순위:** 기술적으로 가능하면 CPU 워커에서 처리
- **대안:** CPU 처리 불가능한 경우, 촬영물(사진/동영상) OMR 인식 기능을 프리미엄 요금제(GPU)로 분리
- **원칙:** 라이트/베이직 플랜에서는 스캔 파일 OMR 자동 채점만 제공. CPU 기반 분석 실패는 시나리오에 있어선 안 됨.

---

## 현재 구조 분석

### ✅ 이미 구현된 기능

#### 1. OMR Grading (`omr_grading`)
- **위치:** `apps/worker/ai_worker/ai/pipelines/dispatcher.py`
- **기능:**
  - 스캔 파일 처리 (`mode="scan"`)
  - 사진 처리 (`mode="photo"` - warp to A4)
  - 자동 모드 (`mode="auto"` - 자동 감지)
  - 식별자 8자리 인식 (`detect_identifier_v1`)
  - OMR 답안 인식 (`detect_omr_answers_v1`)
- **Tier:** Basic (CPU)

#### 2. Homework Video Analysis (`homework_video_analysis`)
- **위치:** `apps/worker/ai_worker/ai/pipelines/homework_video_analyzer.py`
- **기능:**
  - 동영상에서 키 프레임 추출
  - 풀이 여부 감지 (writing detection)
  - 페이지 감지
- **Tier:** Basic (CPU)
- **제한사항:**
  - 현재는 단순히 "글씨가 있는가"만 감지
  - 채점 여부, 풀이 여부, 답안 작성 여부를 구분하지 않음

### ⚠️ 개선이 필요한 부분

#### 1. 서술형 답안지 이미지 추출
- **현재 상태:** 구현되지 않음
- **요구사항:**
  - OMR 답안지에서 서술형 영역만 추출
  - 이미지로 제공하여 조교가 배점 가능하도록

#### 2. 과제 제출 분석 강화
- **현재 상태:** 기본적인 writing detection만 있음
- **요구사항:**
  - 채점 여부 감지 (정답/오답 표시 여부)
  - 풀이 여부 감지 (해설/풀이 과정 존재 여부)
  - 답안 작성 여부 감지 (답안만 작성했는지)
  - 미풀이 문항 추출

#### 3. 동영상 → 이미지 변환
- **현재 상태:** 동영상 분석은 있지만, OMR 처리용 이미지 추출은 없음
- **요구사항:**
  - 동영상에서 최적 프레임 추출
  - OMR 처리 가능한 이미지로 변환

---

## 설계 제안

### 1. Job Type 확장

#### 1.1 새로운 Job Type 추가

```python
# apps/shared/contracts/ai_job.py

AIJobType = Literal[
    "ocr",
    "question_segmentation",
    "handwriting_analysis",
    "embedding",
    "problem_generation",
    "homework_video_analysis",
    "omr_grading",
    # 새로운 타입 추가
    "homework_photo_analysis",      # B 케이스: 사진 기반 과제 분석
    "omr_video_extraction",          # C 케이스: 동영상에서 OMR 이미지 추출
    "essay_answer_extraction",       # A/C 케이스: 서술형 답안지 추출
]
```

#### 1.2 Tier별 Job Type 매핑

```python
# apps/worker/ai_worker/ai/pipelines/tier_enforcer.py

# Lite: OCR만 허용
if tier == "lite":
    allowed_types = ("ocr",)

# Basic: CPU 기반 작업
if tier == "basic":
    allowed_types = (
        "ocr",
        "omr_grading",
        "homework_video_analysis",
        "homework_photo_analysis",      # 추가
        "omr_video_extraction",          # 추가
        "essay_answer_extraction",       # 추가
    )

# Premium: GPU 기반 고성능 작업
if tier == "premium":
    # 모든 작업 허용 + GPU 가속
    allowed_types = ("*",)  # 모든 타입 허용
```

### 2. 기능별 구현 설계

#### 2.1 서술형 답안지 추출 (`essay_answer_extraction`)

**목적:** OMR 답안지에서 서술형 영역만 추출하여 이미지로 제공

**입력:**
```python
payload = {
    "download_url": "...",           # OMR 답안지 이미지
    "template_meta": {...},          # OMR 템플릿 메타데이터
    "essay_question_numbers": [21, 22, 23],  # 서술형 문항 번호
}
```

**출력:**
```python
{
    "extracted_essays": [
        {
            "question_number": 21,
            "image_url": "...",      # 추출된 서술형 답안 이미지
            "bbox": [x, y, w, h],    # 바운딩 박스
        },
        ...
    ],
    "total_extracted": 3,
}
```

**구현 위치:** (모듈화 구조는 [§6.3](#63-dispatcher-모듈화-구조) 참고)
- `apps/worker/ai_worker/ai/pipelines/omr/essay_extractor.py` (신규)
- dispatcher는 `job_type → handler` 라우팅만 담당

**처리 흐름:**
1. OMR 템플릿 메타데이터에서 서술형 문항 위치 확인 (**앵커 포인트 Anchor Points** 활용)
2. **스캔(A) vs 촬영(C) 구분:** 스캔본은 좌표가 정확하나, 촬영본은 왜곡 발생 → **Perspective Transform** 적용 후 정규화된 좌표 사용
3. 정규화된 좌표로 영역 크롭 (글씨가 잘리지 않은 깔끔한 이미지 확보)
4. 이미지 전처리 (리사이즈, 명도 조정)
5. S3에 업로드 후 URL 반환

**설계 원칙 (essay_extractor.py):**
- 템플릿 설계 시 **앵커 포인트**를 반드시 정의 (서술형 영역 4점 좌표)
- 촬영본(C) 처리 시: 앵커를 이용해 Perspective Transform → 정규화된 좌표로 크롭
- 스캔본(A): 동일 좌표계 사용 가능

#### 2.2 과제 사진 분석 강화 (`homework_photo_analysis`)

**목적:** 사진 기반 과제 제출물에서 채점/풀이/답안 작성 여부 감지 (유무 판단만, 논리적 사고 불필요)

**중요:** 논리적 사고는 필요 없음. 단순히 "숙제를 제대로 해왔는가?" 유무 판단만. 유무 판단은 정확해야 함.

**입력:**
```python
payload = {
    "download_url": "...",           # 과제 사진
    "question_count": 10,             # 문항 수 (선택적)
}
```

**출력:**
```python
{
    "has_grading": True,              # 채점 여부 (유무 판단)
    "has_solution": True,             # 풀이 여부 (유무 판단)
    "has_answer": True,               # 답안 작성 여부 (유무 판단)
    "ungraded_questions": [3, 5, 7], # 미채점 문항 번호 (question_count 제공 시)
    "unsolved_questions": [2, 4],    # 미풀이 문항 번호 (question_count 제공 시)
    "unanswered_questions": [1],      # 미답안 문항 번호 (question_count 제공 시)
    "confidence": 0.85,               # 신뢰도 (유무 판단 정확도)
}
```

**구현 위치:** (모듈화 구조는 [§6.3](#63-dispatcher-모듈화-구조) 참고)
- `apps/worker/ai_worker/ai/pipelines/homework/photo_analyzer.py` (신규)
- dispatcher는 `job_type → handler` 라우팅만 담당

**처리 흐름:**
1. 이미지 전처리 (명도 조정, 노이즈 제거)
2. 영역 분석 (문항 영역 분할, question_count 제공 시)
3. **유무 판단:** 단일 알고리즘이 아닌 **다중 신호 기반 점수화** 후 임계값 비교
4. 결과 집계 및 반환

**유무 판단 정확도 확보 전략 (B 케이스, CPU 환경):**

실제 학원 환경 변수(연필 채점, 연한 형광펜, 필압 약한 학생, 배경 노이즈, 빛 반사)를 견디려면 **룰 기반 다중 점수 시스템**으로 설계한다.

| 항목 | 단순 방식 (피하기) | 권장 방식 (정확도 확보) |
|------|-------------------|-------------------------|
| **채점(Grading)** | 빨간색 픽셀 존재 여부만 | **원형(Circle)** 또는 **V자(Check)** 형태의 **컨투어(Contour)** 검출 결합. OpenCV `HoughCircles` 또는 Shape Matching 활용 |
| **풀이(Solution)** | 텍스트 길이만 | 텍스트 영역 **밀도(Density)** + **분산(Variance)**. 풀이는 문항 사이 빈 공간에 무작위 분포 → **Laplacian 필터**로 엣지 강도 측정 (CPU에서 빠름) |
| **답안(Answer)** | OCR만 | 짧은 텍스트/숫자 영역 + 위치 고정 특성 활용 |

**다중 신호 점수화 예시 (채점 유무):**
```python
# 단일 신호가 아닌 가중 합산
grading_score = (
    red_color_score * 0.4 +        # 색상 히스토그램 (빨간/초록 피크)
    checkmark_pattern_score * 0.3  # O/X, V자, 원형 컨투어 매칭
    mark_cluster_score * 0.3       # 마킹 클러스터 밀도
)
has_grading = (grading_score > threshold)
```

동일하게 `solution_score`, `answer_score`를 정의하고, **confidence voting** (여러 영역/프레임에서 일관성 검증)으로 최종 유무 판단 정확도를 높인다.

**Tier:** Basic (CPU) - 룰 기반 다중 점수로 CPU에서 정확도 확보

#### 2.3 동영상에서 OMR 이미지 추출 (`omr_video_extraction`)

**목적:** 동영상에서 최적의 OMR 답안지 프레임 추출

**전략:** 기술적으로 가능하면 CPU에서 처리, 불가능하면 프리미엄(GPU)으로 분리

**입력:**
```python
payload = {
    "download_url": "...",           # 동영상 파일
    "target_type": "omr",             # omr | homework
    "frame_stride": 10,               # 프레임 샘플링 간격
    "min_quality_score": 0.7,         # 최소 품질 점수
}
```

**출력:**
```python
{
    "extracted_frames": [
        {
            "frame_index": 45,
            "timestamp": 3.2,
            "image_url": "...",       # 추출된 이미지 URL
            "quality_score": 0.85,    # 품질 점수 (명도, 선명도, 정렬)
            "is_omr_detected": True,  # OMR 감지 여부
        },
        ...
    ],
    "best_frame": {
        "frame_index": 45,
        "image_url": "...",
        "quality_score": 0.85,
    },
    "total_frames_analyzed": 120,
}
```

**구현 위치:** (모듈화 구조는 [§6.3](#63-dispatcher-모듈화-구조) 참고)
- `apps/worker/ai_worker/ai/pipelines/omr/video_extractor.py` (신규, 또는 `video/omr_extractor.py`)
- dispatcher는 `job_type → handler` 라우팅만 담당

**처리 흐름:**
1. 동영상에서 프레임 **샘플링** (초기 후보 집합)
2. **프레임 선정 우선순위:** 일정 간격(`frame_stride`)만 쓰지 말고, 샘플링된 프레임 중 **Laplacian Variance가 가장 높은(가장 선명한) 프레임**을 우선 분석 대상으로 올림 → **모션 블러(Motion Blur)** 실패율 현저히 감소
3. 각 후보 프레임 품질 평가:
   - 명도 분석 (너무 어둡거나 밝지 않은지)
   - **선명도:** Laplacian variance (가장 핵심 지표)
   - 정렬 분석 (기울기, 왜곡)
   - OMR 패턴 감지 (격자, 마킹 영역)
4. 최적 프레임 선택 후 이미지로 변환 및 S3 업로드

**알고리즘 (CPU 경량화):**
- **품질 점수:** 선명도(Laplacian variance) 비중 상향 → 명도(0.25) + **선명도(0.4)** + 정렬(0.2) + OMR 패턴(0.15)
- **선명도:** Laplacian variance 사용 (CPU 친화적, 모션 블러 구간 자동 제외)
- **정렬:** 간단한 Hough Line Transform
- **OMR 패턴:** 템플릿 매칭 기반

**Tier:** 
- **Basic (CPU):** 경량 알고리즘으로 시도, 실패 시 에러
- **Premium (GPU):** 고급 알고리즘으로 처리 보장

#### 2.4 과제 동영상 분석 강화 (`homework_video_analysis` 개선)

**현재 구현:** 기본적인 writing detection만 있음

**개선 방향:**
- 채점/풀이/답안 작성 여부 감지 추가 (유무 판단만, 논리적 사고 불필요)
- 미풀이 문항 추출 기능 추가

**중요:** 논리적 사고는 필요 없음. 단순히 "숙제를 제대로 해왔는가?" 유무 판단만. 유무 판단은 정확해야 함.

**입력 확장:**
```python
payload = {
    "download_url": "...",
    "frame_stride": 10,
    "min_frame_count": 30,
    "use_key_frames": True,
    "max_pages": 10,
    "processing_timeout": 60,
    # 추가 옵션
    "question_count": 10,             # 문항 수 (선택적, 제공 시 문항별 분석)
}
```

**출력 확장:**
```python
{
    # 기존 필드
    "total_frames": 120,
    "sampled_frames": 12,
    "pages_detected": 3,
    "avg_writing_score": 0.65,
    "filled_ratio": 0.75,
    "frames": [...],
    
    # 추가 필드 (유무 판단)
    "has_grading": True,              # 채점 여부 (유무 판단)
    "has_solution": True,              # 풀이 여부 (유무 판단)
    "has_answer": True,                # 답안 작성 여부 (유무 판단)
    "ungraded_questions": [3, 5],     # 미채점 문항 번호 (question_count 제공 시)
    "unsolved_questions": [2],        # 미풀이 문항 번호 (question_count 제공 시)
    "unanswered_questions": [],       # 미답안 문항 번호 (question_count 제공 시)
    "confidence": 0.85,               # 신뢰도 (유무 판단 정확도)
}
```

**구현 위치:** (모듈화 구조는 [§6.3](#63-dispatcher-모듈화-구조) 참고)
- `apps/worker/ai_worker/ai/pipelines/homework/video_analyzer.py` (기존 개선)

**알고리즘 (경량화):**
- 기존 키 프레임 추출 활용
- 각 프레임에서 채점/풀이/답안 유무 판단 (사진 분석과 동일한 경량 알고리즘)
- 여러 프레임에서 일관성 검증으로 정확도 향상

### 3. GPU 워커 활용 전략 (수정됨)

#### 3.1 요금제별 전략

**핵심 원칙:**
- **라이트/베이직 플랜:** CPU 워커에서 완벽히 처리되어야 함. CPU 기반 분석 실패는 시나리오에 있어선 안 됨.
- **프리미엄 플랜:** GPU 워커 자동 전환 가능

**구현 전략:**
1. **기능 경량화 우선:** CPU에서 처리 가능하도록 알고리즘 최적화
2. **프리미엄 기능 격상:** CPU 처리 불가능한 고급 기능은 프리미엄(GPU)으로 분리

#### 3.2 OMR 처리 전략

**스캔 파일 OMR (A 케이스):**
- **라이트/베이직:** CPU 워커에서 완벽히 처리 (필수)
- **프리미엄:** GPU 가속으로 더 빠른 처리 (선택적)

**촬영물 OMR (C 케이스):**
- **우선:** CPU 워커에서 처리 시도 (기술적으로 가능하면)
- **대안:** CPU 처리 불가능한 경우 프리미엄 요금제(GPU)로 분리
- **구분:**
  - `omr_grading` with `mode="scan"` → 라이트/베이직 (CPU)
  - `omr_grading` with `mode="photo"` or `mode="video"` → 프리미엄 (GPU, CPU 실패 시)

#### 3.3 Tier별 Job Type 재정의

```python
# apps/worker/ai_worker/ai/pipelines/tier_enforcer.py

# Lite: OCR만 허용
if tier == "lite":
    allowed_types = ("ocr",)

# Basic: CPU 기반 작업 (완벽한 처리 보장)
if tier == "basic":
    allowed_types = (
        "ocr",
        "omr_grading",              # mode="scan"만 허용
        "homework_video_analysis",  # 기본 분석만
        "essay_answer_extraction",  # 서술형 추출 (스캔 파일 기반)
    )

# Premium: GPU 기반 고성능 작업
if tier == "premium":
    allowed_types = (
        "ocr",
        "omr_grading",              # 모든 mode 허용 (scan, photo, video)
        "homework_video_analysis",  # 고급 분석
        "homework_photo_analysis",  # 고급 사진 분석
        "omr_video_extraction",     # 동영상 OMR 추출
        "essay_answer_extraction",  # 모든 소스에서 추출
    )
```

#### 3.4 CPU 실패 방지 전략 + 입력 품질 게이트 (Pre-Validation Layer)

**원칙:** 라이트/베이직 플랜에서는 CPU 실패가 발생하지 않도록 설계. "CPU 처리 불가능한 경우 에러 + 프리미엄 안내"만으로는 **운영 중 장애(이상한 사진/동영상으로 인한 실패)** 로 이어질 수 있으므로, **AIJob 생성 전**에 입력을 걸러야 한다.

**반드시 추가할 것: 입력 품질 게이트 (Pre-Validation Layer)**

- **위치:** API 서버 (AIJob 생성 전) 또는 워커 진입 직후
- **함수:** `validate_input_for_basic(tier, job_type, payload) -> (ok: bool, error_message?: str)`
- **Basic/Lite에서 검사할 항목 예시:**
  - **해상도 최소 조건** (예: 최소 600px 짧은 변)
  - **왜곡 정도** (예: 엣지 직선성, 스캔 vs 촬영 추정)
  - **파일 포맷 제한** (예: jpg, png, pdf만; 동영상은 Basic에서 거부 또는 길이 제한)
  - **동영상 길이 제한** (homework_video_analysis 시 최대 N초)
  - **omr_grading:** `mode != "scan"` 이면 Basic에서 **거부** (촬영물 거부 → CS 감소에 현명한 선택)

CPU에서 **무조건 처리 가능한 입력만** Basic으로 허용한다. 이 레이어 없으면 "이상한 사진" 때문에 운영 중 실패가 반복된다.

**기타 방법:**
1. **알고리즘 최적화:** CPU에서 처리 가능한 경량 알고리즘 사용
2. **에러 처리:** 검증 실패 시 명확한 에러 메시지 + 프리미엄 업그레이드 안내
3. **품질 보장:** 스캔 파일에 대해서는 높은 정확도 보장

### 4. API 서버 연동 설계

#### 4.1 정책 기반 성취도 계산

**위치:** API 서버 (AI 워커 아님)

**로직:**
```python
# apps/domains/submissions/services/scoring.py

def calculate_achievement_score(
    analysis_result: dict,
    policy: dict,
) -> float:
    """
    analysis_result: AI 워커에서 받은 분석 결과
    policy: 선생이 설정한 정책
        {
            "no_grading_penalty": 0.1,    # 미채점 시 10% 감점
            "no_solution_penalty": 0.05,   # 미풀이 시 5% 감점
            "no_answer_penalty": 0.2,      # 미답안 시 20% 감점
        }
    """
    base_score = 1.0
    
    if not analysis_result.get("has_grading"):
        base_score -= policy.get("no_grading_penalty", 0.1)
    
    if not analysis_result.get("has_solution"):
        base_score -= policy.get("no_solution_penalty", 0.05)
    
    if not analysis_result.get("has_answer"):
        base_score -= policy.get("no_answer_penalty", 0.2)
    
    return max(0.0, base_score)
```

#### 4.2 서술형 답안지 이미지 제공

**위치:** API 서버

**로직:**
1. AI 워커에서 추출된 서술형 답안지 이미지 URL 받음
2. 학생별로 매칭 (식별자 기반)
3. 프론트엔드에 이미지 URL 제공
4. 조교가 이미지를 보고 배점 입력

---

## 구현 우선순위 (수정됨)

### Phase 1: 필수 기능 (최우선)
1. ✅ **OMR 스캔 파일 자동 채점 완벽화** (`omr_grading` with `mode="scan"`)
   - **A 케이스에서 필수**
   - **라이트/베이직 플랜에서 CPU 워커로 완벽히 처리되어야 함**
   - 현재 구현 개선 필요
   - 구현 난이도: 중
   - 예상 시간: 3-5일

2. ✅ **서술형 답안지 추출** (`essay_answer_extraction`)
   - A 케이스에서 필수
   - 스캔 파일 기반 (라이트/베이직)
   - 구현 난이도: 중
   - 예상 시간: 3-5일

### Phase 2: 기능 강화 (단기)
3. ⚠️ **과제 사진 분석 강화** (`homework_photo_analysis`)
   - B 케이스에서 필수
   - 유무 판단 정확도 중요
   - CPU 경량 알고리즘으로 구현
   - 구현 난이도: 중-높음
   - 예상 시간: 5-7일

4. ⚠️ **과제 동영상 분석 강화** (`homework_video_analysis` 개선)
   - B 케이스에서 필수
   - 유무 판단 정확도 중요
   - CPU 경량 알고리즘으로 구현
   - 구현 난이도: 중-높음
   - 예상 시간: 5-7일

5. 🔄 **정책 기반 성취도 계산** (API 서버)
   - B 케이스에서 필수
   - 구현 난이도: 낮음
   - 예상 시간: 1-2일

### Phase 3: 프리미엄 기능 (중기)
6. 🔄 **촬영물 OMR 인식** (`omr_grading` with `mode="photo"` or `mode="video"`)
   - C 케이스에서 선택적
   - CPU 처리 불가능한 경우 프리미엄(GPU)으로 분리
   - 구현 난이도: 높음
   - 예상 시간: 7-10일

7. 🔄 **동영상에서 OMR 이미지 추출** (`omr_video_extraction`)
   - C 케이스에서 선택적
   - CPU 처리 불가능한 경우 프리미엄(GPU)으로 분리
   - 구현 난이도: 중-높음
   - 예상 시간: 5-7일

---

## 피드백 및 개선사항

### 1. 현재 구조의 장점
- ✅ Tier 시스템으로 CPU/GPU 분리 명확
- ✅ SQS 기반 비동기 처리로 확장성 좋음
- ✅ OMR grading 기본 기능 잘 구현됨
- ✅ 동영상 분석 기본 기능 있음

### 2. 개선이 필요한 부분

#### 2.1 성능 최적화
- **문제:** 동영상 처리 시 시간이 오래 걸림
- **해결:** 키 프레임 추출 최적화, 병렬 처리

#### 2.2 에러 처리
- **문제:** 처리 실패 시 재시도 로직 부족
- **해결:** GPU 워커 Fallback 메커니즘 추가

#### 2.3 모니터링
- **문제:** 처리 시간, 품질 점수 추적 부족
- **해결:** 메트릭 수집 및 대시보드 구축

### 3. 추가 고려사항

#### 3.1 비용 최적화
- CPU 워커로 처리 가능한 작업은 CPU로 처리
- GPU 워커는 정말 필요한 경우만 사용
- EC2 자동 종료 기능 활용 (이미 구현됨)

#### 3.2 정확도 향상
- 템플릿 메타데이터 활용 (이미 구현됨)
- 품질 점수 기반 재처리
- 사용자 피드백 수집 및 모델 개선

#### 3.3 사용자 경험
- 처리 진행 상황 실시간 업데이트
- 실패 시 명확한 에러 메시지
- GPU 워커 제안 시 사용자 확인

---

## 6. 구조 보강 (실무 리뷰 반영)

전체 설계는 **진행해도 됨**이 맞고, 구현에 들어가기 전에 아래 **3가지 구조 보강**을 반드시 적용하는 것을 권장한다.

### 6.1 입력 품질 게이트 (Pre-Validation Layer)

**문제:** CPU 완결 보장이 “실패 시 에러 + 프리미엄 안내” 수준으로만 있으면, 운영 중 **이상한 사진/동영상** 때문에 Basic에서 반복 실패 → 장애·CS로 이어진다.

**해결:** AIJob 생성 **전**에 입력을 검증하는 레이어를 둔다.

| 항목 | 내용 |
|------|------|
| **위치** | API 서버 (AIJob 생성 전) 또는 워커 진입 직후 |
| **함수** | `validate_input_for_basic(tier, job_type, payload) -> (ok: bool, error_message?: str)` |
| **Basic/Lite 검사 예시** | 해상도 최소 조건, 왜곡 정도, 파일 포맷 제한, 동영상 길이 제한 |
| **omr_grading** | Basic에서는 `mode == "scan"` 만 허용, **촬영물 거부** (CS 감소에 유리) |

CPU에서 **무조건 처리 가능한 입력만** Basic으로 허용한다.

### 6.2 Homework 유무 판단 정확도 전략 (다중 신호 기반)

**문제:** “색상 히스토그램 + 텍스트 밀도”만 쓰면, 연필 채점·연한 형광펜·필압 약함·배경 노이즈·빛 반사 등에서 오판이 난다.

**해결:** 유무 판단을 **단일 알고리즘이 아닌, 룰 기반 다중 점수 시스템**으로 설계한다.

- **채점:** 색상 + **원형/V자 컨투어 검출** (HoughCircles, Shape Matching) 결합
- **풀이:** 텍스트 **밀도·분산** + **Laplacian 엣지 강도** (문항 사이 빈 공간 분포 특성 활용)
- **답안:** 짧은 텍스트/숫자 + 위치 고정 특성

최종 유무는 예: `grading_score = red_color_score*0.4 + checkmark_pattern_score*0.3 + mark_cluster_score*0.3` 처럼 가중 합산 후 `threshold` 비교. 여러 영역/프레임에서 **confidence voting**으로 정확도를 높인다. CPU만으로도 구현 가능하다.

### 6.3 Dispatcher 모듈화 구조

**문제:** 모든 job 처리 로직이 `dispatcher.py` 한 곳에 늘어나면 유지보수·테스트·의존성 관리가 어려워진다.

**해결:** dispatcher는 **오직 `job_type → handler` 라우팅만** 담당하고, 실제 처리 로직은 도메인별 모듈로 분리한다.

**권장 디렉터리 구조:**

```
apps/worker/ai_worker/ai/
├── pipelines/
│   ├── omr/
│   │   ├── grading.py           # omr_grading
│   │   ├── essay_extractor.py   # essay_answer_extraction
│   │   └── video_extractor.py   # omr_video_extraction (동영상 → 이미지)
│   ├── homework/
│   │   ├── photo_analyzer.py     # homework_photo_analysis
│   │   └── video_analyzer.py     # homework_video_analysis (기존 개선)
│   └── dispatcher.py            # job_type → handler 매핑만
```

**dispatcher 예시:**

```python
# dispatcher.py: 라우팅만
def handle_ai_job(job: AIJob) -> AIResult:
    handlers = {
        "ocr": ocr.handler,
        "omr_grading": omr.grading.handler,
        "essay_answer_extraction": omr.essay_extractor.handler,
        "omr_video_extraction": omr.video_extractor.handler,
        "homework_photo_analysis": homework.photo_analyzer.handler,
        "homework_video_analysis": homework.video_analyzer.handler,
    }
    h = handlers.get(job.type)
    if not h:
        return AIResult.failed(job.id, f"Unsupported job type: {job.type}")
    return h(job)
```

### 6.4 기술 검토 요약 (Architecture Check)

| 항목 | 검토 결과 | 비고 |
|------|-----------|------|
| Job Type 확장 | 적절함 | `omr_video_extraction`, `homework_photo_analysis` 분리 좋음 |
| Tier Enforcer | 강력 추천 | Basic에서 "촬영물 거부" 정책은 CS 감소에 현명한 선택 |
| API 계산 로직 | 합리적 | AI는 팩트(유무)만 전달, 비즈니스 로직(감점)은 API 서버 담당 → 유연 |

---

## 결론

현재 AI 워커 구조는 잘 설계되어 있으며, 실무 시나리오를 지원하기 위해 다음 원칙과 기능들이 필요합니다:

### 핵심 원칙
1. **라이트/베이직 플랜:** CPU 워커에서 완벽히 처리되어야 함. CPU 기반 분석 실패는 시나리오에 있어선 안 됨.
2. **프리미엄 플랜:** GPU 워커 자동 전환 가능, 고급 기능 제공
3. **기능 경량화 우선:** CPU에서 처리 가능하도록 알고리즘 최적화
4. **프리미엄 기능 격상:** CPU 처리 불가능한 고급 기능은 프리미엄(GPU)으로 분리

### 필수 기능
1. **OMR 스캔 파일 자동 채점 완벽화** - A 케이스 필수, 라이트/베이직에서 CPU 완벽 처리
2. **서술형 답안지 추출** - A 케이스 필수, 스캔 파일 기반
3. **과제 분석 강화** - B 케이스 필수, 유무 판단 정확도 중요, CPU 경량 알고리즘

### 선택적 기능 (프리미엄)
4. **촬영물 OMR 인식** - C 케이스 선택적, CPU 불가능 시 프리미엄으로 분리
5. **동영상에서 OMR 이미지 추출** - C 케이스 선택적, CPU 불가능 시 프리미엄으로 분리

이러한 원칙과 기능들을 단계적으로 구현하면 실무 요구사항을 충족할 수 있다.

**구현 전 필수 보강 (실무 리뷰):** 그대로 구현에 들어가기 전에 **§6 구조 보강** 3가지를 반드시 적용할 것.

1. **입력 품질 게이트** – Basic에서 실패 없는 구조를 위한 Pre-Validation Layer  
2. **Homework 유무 판단** – 다중 신호 기반 점수화 (정확도 확보)  
3. **Dispatcher 모듈화** – `job_type → handler` 라우팅만 두고, omr/homework 파이프라인 분리
