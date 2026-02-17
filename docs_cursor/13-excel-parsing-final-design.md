# 엑셀 파싱 최종 설계 보고서 (운영 안정 모드)

**일자**: 2026-02-17  
**대상**: `src/application/services/excel_parsing_service.py`  
**모드**: Parent Phone Mandatory + AI Hybrid  
**목표**: 학부모 전화 100% 필수 보장 / 데이터 무결성 최우선

> **설계 리뷰 보완 4가지 반영 완료** (2026-02-17)

---

## 1. 개요

학원마다 상이한 엑셀 양식을 자동 인식하되, 학부모 전화(parent_phone)를 필수 필드로 강제하는 안정형 파싱 구조로 개선하였다.

- 학부모 전화가 없거나 형식이 잘못된 경우 **업로드 전체 실패 (Fail-Fast)**
- 규칙 기반 1차 추론 + AI 워커 2차 판정 하이브리드 적용
- 학원별 매핑 학습 구조 확장 가능

---

## 2. 필수 / 선택 필드 정책

| 구분 | 필드 | 정책 |
|------|------|------|
| **필수** | 이름 | 유효 이름 없으면 해당 행 제외 |
| **필수** | 학부모 전화 | 010 10~11자리 필수, 없으면 전체 업로드 실패 |
| 선택 | 학생 전화 | 공란 허용 |
| 선택 | 학교, 성별, 메모 등 | 존재 시 매핑 |

### 중요 정책

- 학부모 전화는 학생 전화로 **대체하지 않는다**.
- 학부모 전화 누락/형식 오류가 **한 행이라도** 존재하면 전체 롤백.
- 부분 성공 금지.

---

## 3. 전체 처리 파이프라인

```
엑셀 업로드
    ↓
헤더 행 탐지
    ↓
Header 정규화
    ↓
Rule 기반 1차 컬럼 점수화
    ↓ (conf < 0.9 인 경우만)
AI 워커 2차 판정
    ↓
parent_phone 확정
    ↓
행 단위 Fail-Fast 검증
    ↓ (순서 필수: 학생 행 판별 → parent_phone 검증)
학생 생성
```

---

## 4. 헤더 정규화

- 전각 숫자 → 반각 변환 (０１２３ → 0123)
- 전각 공백 제거
- 공백/특수문자 제거
- lower-case 처리  
→ `_normalize_header(header)`

---

## 5. Rule 기반 1차 parent_phone 판정

### 키워드

- **PARENT_KEYWORDS**: 부모, 학부모, 보호자, guardian
- **STUDENT_KEYWORDS**: 학생

### phone_ratio

- 010 시작, 숫자 10~11자리
- 컬럼 내 유효 비율 계산

### 점수 계산

| 조건 | 점수 |
|------|------|
| parent 키워드 포함 | +0.6 |
| student 키워드 포함 | -0.5 |
| phone_ratio × 0.7 | 가산 |
| **중복도 가산** (부모/학생 키워드 둘 다 없을 때) | duplicate_ratio × 0.3 |

**중복도 기반 가산**: 헤더에 "부모"/"학생" 키워드가 모두 없는 경우(예: 연락처1, 연락처2), 데이터 중복 비율로 parent 가능성 판단. 부모 전화는 형제·자매로 인해 여러 행에서 같은 번호가 반복될 가능성이 높음. `duplicate_ratio = 1 - (unique수 / 전체수)`.

### 판정 기준

| 점수 | 처리 |
|------|------|
| ≥ 0.9 | parent_phone 확정 |
| 0.6~0.9 | AI 워커 판정 |
| < 0.6 | 후보 제외 |

---

## 6. AI 워커 2차 판정

### 호출 조건

- rule_confidence < 0.9
- 0.6 ≤ rule_confidence

### 복수 후보 처리 (강제)

- phone_candidate가 2개 이상일 경우 **전체 후보를 AI에 동시 전달**
- AI는 반드시 **하나의 parent_phone_col_index만** 반환해야 함
- AI가 null 반환 또는 confidence < 0.8 → **업로드 실패**

### AI 입력

- header, 샘플 값 최대 5개 (후보 컬럼별)
- 전화번호 반드시 마스킹 — **포맷 다양성 유지**
  - `010-1234-5678` → `010-****-5678`
  - `01012345678` → `010****5678`
  - `010.1234.5678` → `010.****.5678`

### AI 출력 (JSON 강제)

```json
{
  "parent_phone_col_index": <int|null>,
  "confidence": <0..1>
}
```

### 채택 기준

- confidence ≥ 0.8 → 채택
- < 0.8 → 업로드 실패

---

## 7. Fail-Fast 검증 및 상세 리포트

```python
def validate_parent_phone(phone):
    phone = re.sub(r"\D", "", str(phone))
    return phone.startswith("010") and len(phone) in (10, 11)
```

- parent_phone 컬럼 미확정 → 즉시 실패
- **parent_phone 검증은 반드시 `_row_looks_like_student()`로 필터링된 학생 행에만 적용** (소제목·날짜 행 제외)
- parent_phone 컬럼이 존재하더라도 **모든 학생 행에서 유효한 010 번호가 없으면** 업로드 실패
- 하나라도 실패 시 전체 롤백

### 상세 리포트 (2026-02-17 반영)

실패 시 **몇 번째 행의 어떤 값이 잘못되었는지**를 목록으로 반환. `ExcelValidationError.errors`:

```python
errors = [
    {"row": 5, "value": "02-1234-5678...", "reason": "학부모 전화번호가 없거나 형식이 잘못되었습니다(010 10~11자리)."},
    {"row": 12, "value": "(비어있음)", "reason": "..."},
]
```

---

## 8. 행 단위 학생 여부 판별

### 제외 패턴

- 01월, 2/7~, 날짜 안내 행
- 순수 숫자, 20자 초과 긴 문자열

### 포함 조건 (2개 이상 충족)

- 유효 이름 패턴 (2~5자 한글/알파벳)
- 유효 전화번호 존재
- 출/보/결/부재 등 출결 단문

---

## 9. 학원별 매핑 학습 및 유사도 체크 (확장 구조)

- `academy_id`, `normalized_header`, `field`, `confidence`, `last_seen` 저장
- 업로드 성공 시 mapping 저장
- 동일 학원 재업로드 시 기존 mapping 우선 적용
- AI 호출 최소화 (현재 확장 포인트만 구현)

### 헤더 유사도 검사 (2026-02-17 반영)

`_header_similarity(h1, h2)` — `difflib.SequenceMatcher` 기반 0~1 유사도.

- 기존 매핑이 있을 때: `similarity(cached_header, current_header) >= 0.8` 이면 캐시 사용
- 유사도가 낮으면 매핑 갱신 유도 (재추론)

---

## 10. 보안 정책

- OpenAI 호출은 AI 워커 내부에서만 수행
- 전체 엑셀 데이터 전송 금지
- 샘플 데이터만 전송
- 전화번호 반드시 마스킹

---

## 11. 구현 파일

| 파일 | 역할 |
|------|------|
| `src/application/services/excel_parsing_service.py` | 파싱·검증·Rule 1차·AI 호출 |
| `apps/worker/ai_worker/ai/excel_schema_infer.py` | AI parent_phone 2차 판정 |

---

## 12. 배포

- AI 워커 이미지 재빌드
- ECR 푸시
- ASG Instance Refresh 수행
- 로그에서 AI 호출 정상 여부 확인

---

## 13. 설계 리뷰 보완 사항 반영 요약

| # | 보완 제안 | 반영 내용 |
|---|----------|----------|
| 1 | parent_phone 판정 고도화 | 헤더에 부모/학생 키워드 둘 다 없을 때 `duplicate_ratio` 가산 (부모 전화는 형제로 인해 중복 가능성 높음) |
| 2 | AI 마스킹 형태 유지 | `_mask_phone_for_ai`: 010-1234-5678 → 010-****-5678, 010.1234.5678 → 010.****.5678 등 포맷 유지 |
| 3 | Fail-Fast 상세 리포트 | `ExcelValidationError.errors`: `[{"row": N, "value": "...", "reason": "..."}]` 형태로 전체 오류 목록 반환 |
| 4 | 학원별 매핑 + 유사도 | `_header_similarity` 유틸 추가, `_get_academy_parent_mapping` 확장 시 유사도 >= 0.8 조건 적용 구조 |

---

## 최종 결론

이 구조는:

- 학부모 전화 필수 정책을 강제하며
- 학원마다 다른 엑셀 양식에 대응 가능하고
- 운영 중 데이터 오염을 원천 차단하며
- 설계 리뷰 4가지 보완 사항을 반영하여 판정·마스킹·검증·매핑 영역을 고도화하였다.
