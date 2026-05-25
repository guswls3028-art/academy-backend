# OMR 자동채점 시스템 — SSOT

## 개요

학원 시험의 객관식 답안을 OMR 답안지로 수집하고 AI 워커로 자동 채점하는 시스템.

## SSOT 구조

| 구성요소 | SSOT 파일 | 역할 |
|----------|-----------|------|
| **답안지 디자인** | `frontend/public/omr-sheet.html` | 인쇄/PDF용 OMR 답안지 (브라우저 렌더링) |
| **좌표 메타** | `backend/apps/domains/assets/omr/services/meta_generator.py` | mm 단위 버블/ROI 좌표 (AI 워커용) |
| **답안 검출** | `backend/apps/worker/ai_worker/ai/omr/engine.py` | 스캔 이미지에서 마킹 감지 |
| **식별자 검출** | `backend/apps/worker/ai_worker/ai/omr/identifier.py` | 전화번호 뒤 8자리 감지 |

## 레이아웃 (A4 Landscape, 297×210mm)

```
┌──────────────────────────────────────────────────────────┐
│ L-mark                                           L-mark │
│                                                          │
│  ┌─Left(62mm)──┐  3mm  ┌─MC(44mm)─┐ 2.5 ┌─MC(44mm)─┐  │
│  │ [Logo]      │       │ 1  12345 │     │16  12345 │  │
│  │ 시험명      │       │ 2  12345 │     │17  12345 │  │
│  │             │       │ ...      │     │ ...      │  │
│  │─────────────│       │15  12345 │     │30  12345 │  │
│  │ 성명        │       └──────────┘     └──────────┘  │
│  │─────────────│                                       │
│  │ 전화8자리   │       ┌─서술형(flex)─────────────────┐│
│  │ [XXXX-XXXX] │       │ 1  [작성 영역]              ││
│  │ [0-9 버블]  │       │ 2  [작성 영역]              ││
│  │─────────────│       │ ...                          ││
│  │ 작성안내    │       └──────────────────────────────┘│
│  └─────────────┘                                       │
│                                                          │
│ L-mark                                           L-mark │
└──────────────────────────────────────────────────────────┘
```

## 좌표 체계 (meta_generator.py)

### 페이지 상수
- 페이지: 297mm × 210mm
- 마진: 좌10, 상9, 우10, 하6 (mm)
- 좌측 패널: 62mm 폭
- 객관식 컬럼: 44mm 폭 (고정), 최대 3컬럼
- 컬럼 간격: 2.5mm

### 버블
- 쌀톨형 세로 타원: 3.6mm × 5.2mm
- 선택지: "1"~"5" (숫자)
- 식별번호: "0"~"9" (세로 10행 × 가로 8열)

### 식별자 (전화번호 뒤 8자리)
- 4자리 - 4자리 구조
- 기입칸 8개 + 아래 0~9 버블 그리드
- 학생 본인 휴대폰 번호 우선, 없으면 부모님 번호

## 채점 대상 SSOT

- **OMR 채점 대상의 기준은 시험이 연결된 차시의 `SessionEnrollment` roster다.**
- `ExamEnrollment`는 시험별 명시 대상자이자 기존 API 호환 레이어다. OMR 업로드/학생 매칭/성적 수동입력 시점에 차시 roster 학생이면 자동으로 materialize할 수 있으며, OMR 채점의 선행 조건이 아니다.
- 후보 학생은 항상 같은 tenant, 활성 `Enrollment`, 삭제되지 않은 학생으로 제한한다. 다른 tenant나 시험이 연결되지 않은 차시의 학생으로 fallback하지 않는다.
- 성적탭 row 모수는 차시 출석/수강 roster다. 시험 점수 셀은 `ExamEnrollment`가 없어도 차시에 붙은 시험의 OMR/수동입력 대상 학생에게 보여야 한다.
- 오인식/미식별 스캔은 `Submission`의 수동 검토 상태와 답안 보정 API를 통해 보정한다. 원본 운영 데이터를 임의로 수정하지 않고, 검토자가 선택적으로 답안/점수를 확정한다.

## 운영 UX SSOT

- 선생/원장은 **강의 > 차시 > 성적** 화면에서 OMR을 등록한다. 별도 도구 화면은 OMR 양식 생성/출력용 보조 도구이며, 차시 채점의 주 동선이 아니다.
- 성적 화면의 주 CTA는 `OMR 스캔 등록`이다. 시험이 1개면 바로 업로드 모달을 열고, 여러 개면 시험 선택만 거쳐 같은 업로드 모달로 진입한다.
- 시험 설정/제출관리 화면은 OMR 스캔을 직접 등록하지 않는다. 등록이 필요하면 성적 탭으로 이동시키고, 해당 화면은 출력/대상자/제출 확인/재채점 보조 역할만 맡는다.
- `수강생 일괄배정`은 자동 materialize 실패나 운영 보정용 보조 기능이다. 초보 사용자 기본 흐름에서는 숨기고 더보기 메뉴에 둔다.
- 업로드 화면은 "파일 선택 -> 등록 시작 -> 성적표/드로어에서 결과 확인"으로 읽혀야 한다. OMR 스캔 등록을 위해 사용자가 여러 화면을 이해해야 하는 설계를 만들지 않는다.
- 학생 상세 드로어는 OMR 스캔 썸네일/정렬된 미리보기와 수동 답안 보정 진입점을 제공해야 한다. 자동 인식이 틀릴 수 있음을 전제로, 보정은 선택적으로 가능해야 한다.

## 자동채점 파이프라인

```
1. 선생님: OMR 답안지 인쇄 (omr-sheet.html)
2. 학생: 답안지에 마킹 (사인펜)
3. 선생님: 스캔 파일 업로드 (batch upload)
4. 시스템:
   a. warp.py → A4 landscape로 보정 (90/180/270도 회전 포함)
   b. identifier.py → 전화번호 8자리 추출 → 학생 매칭
   c. engine.py → 객관식 버블 감지
   d. ai_omr_result_mapper.py → Submission에 결과 반영
   e. grader.py → 정답 대조 → ExamResult 생성
5. 선생님: 결과 확인, 필요 시 수동 보정
```

## 문항 구성

| 문항 수 | 컬럼 분할 |
|---------|----------|
| 1~20 | 1컬럼 |
| 21~40 | 2컬럼 (균등 분할) |
| 41~45 | 3컬럼 (균등 분할) |

서술형은 별도 컬럼 (번호 독립: 1번~)

## 프론트엔드 연동

### 성적 탭
- `/admin/lectures/{lectureId}/sessions/{sessionId}/scores`: OMR 스캔 등록 주 동선
- `SessionOmrUploadAction.tsx`: 시험 선택 + 스캔 업로드 모달

### 시험 탭
- `ExamPolicyPanel.tsx`: 답안 등록 후 "OMR 답안지 출력" 버튼 자동 노출
- `ExamSubmissionsPanel.tsx`: 제출 목록/파일 확인. 스캔 등록은 성적 탭으로 이동.
- `ExamBulkActionsPanel.tsx`: 재채점 실행. 스캔 등록은 성적 탭으로 이동.
- 문항 수에 맞는 URL 파라미터로 omr-sheet.html 호출

### 도구 탭
- `/admin/tools/omr`: OMR 생성기 (독립 도구)
- 시험명/강의명/차시명/문항수 설정 → 미리보기 → 인쇄

### URL 파라미터
```
/omr-sheet.html?exam=시험명&lecture=강의명&session=차시명&mc=30&essay=5&choices=5
```

## API 엔드포인트

| Method | Path | 설명 | 상태 |
|--------|------|------|------|
| GET | `/exams/{id}/omr/defaults/` | OMR 기본값(시험명, 문항수 등) 조회 | **현행** |
| POST | `/exams/{id}/omr/preview/` | OMR 미리보기 렌더링 | **현행** |
| POST | `/exams/{id}/omr/pdf/` | OMR PDF 생성·다운로드 | **현행** |
| POST | `/exams/{id}/generate-omr/` | OMR 메타 + URL 반환 | ⚠️ **deprecated** |
| GET | `/assets/omr/objective/meta/` | 좌표 메타 조회 | 현행 |
| POST | `/submissions/exams/{id}/omr/batch/` | 스캔 파일 일괄 업로드 | 현행 |

## 버전 이력

| 버전 | 날짜 | 변경 |
|------|------|------|
| v15.1 | 2026-05-26 | 차시 성적 화면 OMR 등록을 주 동선으로 고정. 시험 선택/업로드/보정 UX와 `SessionEnrollment` roster 기준 채점 정책을 SSOT에 명시. |
| v14 | 2026-04 | reportlab 기반 `pdf_renderer.py`로 재구현. `/omr/defaults/`, `/omr/preview/`, `/omr/pdf/` 3종 엔드포인트 추가. `generate-omr/`은 deprecated. |
| v7 | 2026-03-19 | HTML SSOT 기반 전면 재설계. 기존 v245_final.py 삭제. |
| v245_final | ~ 2026-03-18 | 구 reportlab 기반 PDF 렌더러 (삭제됨) |
