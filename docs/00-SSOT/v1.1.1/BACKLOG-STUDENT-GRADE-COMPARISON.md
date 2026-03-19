# 백로그: 학생 성적 비교 시스템

> **상태:** 백로그 (조건부 추진)
> **작성일:** 2026-03-19
> **구현 조건:** 아래 3가지 모두 충족 시에만 추진

## 추진 조건

| # | 조건 | 설명 |
|---|---|---|
| 1 | 학원이 석차 노출을 원할 때 | 테넌트 설정으로 on/off. 기본값 off. |
| 2 | 최소 인원 기준 충족 시에만 | 비교 대상 학생 수 N명 이상일 때만 표시 (예: 5명) |
| 3 | percentile 중심 완화 표현 | 석차(n등) 직접 노출 대신 상위 %ile 중심. 고도화 단계에서만 석차 추가. |

---

## 배경

- 강의 > 차시(수업) > 시험/과제 구조
- 시험 템플릿 개념 존재 (하나의 템플릿 → 여러 강의/차시에서 사용)
- 과제도 템플릿 재사용 저장 구조 존재
- 현재 학생 성적 조회는 개인 점수만 제공 (비교 데이터 없음)
- 어드민에는 평균/최고/최저/합격률/문항별 정답률 등 통계 이미 존재

## 비교 뷰 3종

### 1. 차시 단위 비교

- 비교 대상: 같은 SessionEnrollment 학생들
- 표시: 내 점수 + 상위 %ile + 평균 대비 + 분포
- 진입점: 세션 상세 → 성적 탭

### 2. 강의 단위 비교

- 비교 대상: 같은 Enrollment(lecture) 학생들
- 표시: 누적 통계 + 차시별 추이 그래프 + 상위 %ile
- 진입점: 성적 허브 → 강의별 탭

### 3. 시험(템플릿) 단위 비교

- 비교 대상: `effective_template_exam_id`가 같은 모든 Result (강의 무관)
- 표시: 동일 시험 전체 응시자 중 상위 %ile + 평균/분포
- 진입점: 시험 결과 페이지

## 백엔드 API 설계 (안)

```
GET /student/grades/session/{session_id}/rank/
GET /student/grades/lecture/{lecture_id}/rank/
GET /student/grades/exam/{exam_id}/rank/
```

공통 응답:
```json
{
  "my_score": 85,
  "max_score": 100,
  "percentile": 89.3,
  "total_students": 28,
  "stats": { "average": 72.5, "highest": 98, "lowest": 41, "median": 74 },
  "distribution": [{ "range": "90-100", "count": 4 }, ...]
}
```

- `rank` 필드는 테넌트 설정 on일 때만 포함
- `total_students < 최소 인원` 이면 전체 응답을 `null`로 반환

## 프론트엔드 뷰 (안)

| 뷰 | UI |
|---|---|
| 차시 비교 | 내 점수 + 상위 %ile 뱃지 + 분포 바 차트 |
| 강의 비교 | 누적 상위 %ile + 차시별 추이 라인 차트 + 통계 카드 |
| 시험 비교 | 상위 %ile + 평균 대비 + 점수 히스토그램 |

## 부수 작업: 과제 재사용 UX 개선

- 과제 생성 모달에 "기존 과제에서 가져오기" 셀렉터 추가
- 시험 템플릿 선택과 동일 패턴
- 현재 `template_homework_id` 저장 구조는 존재하나 UI가 미흡

## 리스크

| 항목 | 대응 |
|---|---|
| 소수 인원 강의 (3명 등) | 최소 인원 미만 시 비교 미표시 |
| 석차 민감도 | percentile 중심, 석차는 설정 on 시에만 |
| 템플릿 미연결 시험 | 자기 자신만 그룹 → 해당 세션 응시자만 비교 |
| 성능 | DB `RANK()` 윈도우 함수, 필요시 캐시 |

## 기존 인프라 활용

- `Exam.effective_template_exam_id` — 템플릿 그룹핑 키
- `Result` 모델 — enrollment_id 기준 최신 점수
- `SessionScoreSummaryService` — 어드민 통계 로직 재사용 가능
- `build_lecture_results_snapshot()` — 강의 단위 집계 함수 존재
