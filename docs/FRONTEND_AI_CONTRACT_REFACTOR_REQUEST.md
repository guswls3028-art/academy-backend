# 프론트엔드 전면 리팩토링 요청 — 새 AI 계약(Contract) 기준

**문서 목적:** AI 워커/백엔드가 새 상태 머신·요금제 정책으로 재설계된 상태에서, 구형 상태 기준의 프론트와의 정책 불일치를 해소하기 위한 **전면 리팩토링** 요청 명세.

**요청 시 사용 문구 (복붙용):**

---

현재 AI 워커/백엔드가 **새로운 상태 머신 및 요금제 정책**을 기반으로 재설계되었음.  
구형 프론트는 기존 `PENDING` / `DONE` / `FAILED` 구조를 전제로 작성되어 있어 **정책 불일치**가 발생함.

따라서 **“API 스펙 맞게 수정”이 아니라**, 아래 **새 계약(Contract) 기준으로 프론트를 전면 리팩토링** 요청함.

---

## 1. 배경: 왜 부분 수정이 아닌 전면 리팩토링인가

| 구분 | 예전 (구형) | 지금 (백엔드/워커 반영 완료) |
|------|-------------|------------------------------|
| **상태 모델** | PENDING / RUNNING / DONE / FAILED | VALIDATING, REJECTED_BAD_INPUT, DONE, FAILED(Premium만), REVIEW_REQUIRED(Premium만), FALLBACK_TO_GPU, RETRYING |
| **Lite/Basic 실패** | FAILED → 실패 UI | **실패 없음** → 애매한 경우 DONE + `review_candidate` |
| **거부** | 단순 실패 처리 | **REJECTED_BAD_INPUT** + `rejection_code` (사용자 안내 문구 매핑) |
| **식별자** | (없음 또는 미반영) | **NEEDS_IDENTIFICATION** + 조교 매칭 흐름 |

→ 상태 기반 UI 설계와 UX 흐름이 **처음부터 다시 설계**되어야 함.

---

## 2. 리팩토링 범위 (필수 반영 항목)

### 🔥 구형 상태 가정 코드 전면 제거 (Mandatory)

프론트가 가장 많이 망하는 지점은 **기존 코드 유지 + 새 코드 추가** 방식이다.  
아래 항목은 **절대 제거 대상**이며, 보존하지 말고 제거 후 재구성한다.

- `status === "FAILED"` 를 Lite/Basic 기준으로 처리하는 **기존 분기 전면 삭제**
- 구형 4단계 상태(`PENDING` / `RUNNING` / `DONE` / `FAILED`)에 의존하는 **switch/if 분기 제거**
- **“FAILED면 무조건 에러 UI”** 같은 공통 컴포넌트 **제거** 또는 **Premium 전용으로 제한**
- 상태 문자열 **하드코딩 제거** → **enum 기반 타입** 사용

**기존 상태 가정 로직은 보존하지 말고 제거 후 재구성한다.**  
이 원칙이 없으면 리팩토링이 아니라 “덧붙이기”가 된다.

---

### 2.1 AI Job 상태(enum) 전체 기준으로 상태 UI 재설계

**새 상태 enum (백엔드와 동일하게 유지):**

- `PENDING`
- `VALIDATING`
- `RUNNING`
- `DONE`
- `FAILED` — **Premium에서만** 노출 (Lite/Basic은 이 상태로 오지 않음)
- `REJECTED_BAD_INPUT` — 거부 정책 해당 (Pre-Validation)
- `FALLBACK_TO_GPU`
- `RETRYING`
- `REVIEW_REQUIRED` — **Premium/조교 큐 전용** (Lite/Basic에는 미노출)

프론트에서:

- AI Job 상세/목록/배지 등 **모든 상태 표시**를 위 enum 기준으로 재정의.
- 라벨·색상·아이콘을 새 상태별로 정의하고, 구형 4개 상태 가정 코드 제거.

**상태별 UX 정의 표 (화면에 무엇을 보여줄지):**

| status | Lite/Basic UX | Premium UX |
|--------|----------------|------------|
| PENDING | 처리 대기 | 처리 대기 |
| VALIDATING | 업로드 검증 중 | 동일 |
| REJECTED_BAD_INPUT | 재촬영 유도 메시지 (rejection_code 매핑) | 동일 |
| RUNNING | 분석 중 | 분석 중 |
| DONE | 완료 (review_candidate 시 배지/표시 가능) | 완료 |
| REVIEW_REQUIRED | 사용 안 함 | 조교 검토 필요 |
| FAILED | 사용 안 함 | 분석 실패 |
| FALLBACK_TO_GPU | 노출 안 함 | “고급 분석 중” 표시 가능 |
| RETRYING | 재시도 중 | 재시도 중 |

이 표를 기준으로 상태별 라벨·배지·메시지를 구현한다.

### 2.2 Lite/Basic “실패 없음” 정책 반영

- Lite/Basic 구간에서는 **FAILED 상태가 오지 않음** (백엔드 정책).
- “실패” 문구/화면을 **Lite/Basic 전용으로 제거하거나**,  
  실제로는 **DONE + review_candidate**인 경우에 한해 “검토 후보” 등으로만 표시.
- **실패(FAILED) UI는 Premium 또는 조교/운영용 경로에서만** 사용.

### 2.3 `review_candidate` 플래그 처리

- 결과 payload에 `flags.review_candidate === true` 인 경우:
  - “검토 후보”, “확인 필요” 등 **별도 표시/배지** 제공.
  - 조교 검토 큐(또는 검토 후보 목록) UX와 연동.
- Lite/Basic에서 “실패” 대신 **“완료 + 검토 후보”** 로만 노출되도록 로직 정리.

### 2.4 REJECTED_BAD_INPUT UX 분리

- 상태가 `REJECTED_BAD_INPUT` 인 경우:
  - **rejection_code** 를 받아, 사용자 안내 문구로 매핑하여 표시.
  - 백엔드 정의 코드 예:  
    `RESOLUTION_TOO_LOW`, `FILE_TOO_LARGE`, `VIDEO_TOO_LONG`, `BLUR_OR_SHAKE`, `TOO_DARK`, `INVALID_FORMAT`, `OMR_PHOTO_NOT_ALLOWED`
- “일반 실패”와 **거부(재촬영/재업로드 유도)** 를 UX·문구에서 명확히 구분.

### 2.5 NEEDS_IDENTIFICATION 처리 흐름 반영

- Submission 상태 `needs_identification` (이미 statusMaps 등에 있을 수 있음) 을 **새 계약 기준**으로 정리:
  - 식별자(omr_code) 매칭 실패/불확실 → 자동 점수 반영 중단 → **검토 큐(조교 매칭)** 로 유도.
- “점수 반영이 막혀도 이유를 모름” 이 없도록:
  - **식별자 확인 필요** 문구 및 조교 매칭/검토 큐 진입 경로를 명시.

---

## 3. 참고: 백엔드 계약 요약

- **AI Job status:** 위 2.1 enum. Premium만 FAILED/REVIEW_REQUIRED.
- **Lite/Basic:** 항상 DONE + 필요 시 `flags.review_candidate`; FAILED 미사용.
- **Pre-Validation 거부:** `ok: false`, `rejection_code`, `error` (메시지) 반환; job 미생성.
- **Submission:** `needs_identification` + `meta.manual_review.required` / `reasons` 등으로 식별자·수동 검토 상태 전달.

상세 스펙은 `docs/AI_WORKER_REAL_WORLD_DESIGN_FINAL.md` 및 API 스펙 문서 참고.

---

## 4. 결론

- 이 요청은 **“버그 수정”이 아니라 “상태 기반 UI 아키텍처 재정렬”** 이다.
- **새 계약(Contract) 기준으로 재구성** 하되, 위 5가지(상태 enum, Lite/Basic 정책, review_candidate, REJECTED_BAD_INPUT, NEEDS_IDENTIFICATION)를 반드시 포함하여 프론트를 전면 리팩토링할 것.

---

## 5. 계약 변경 금지 원칙

AI Job status enum은 **프론트와의 계약**이므로, 변경 시 반드시 **Contract Versioning**을 적용한다.  
백엔드가 상태를 추가/변경하면 프론트가 전부 다시 깨지므로, 버전 관리·공지·마이그레이션 가이드를 함께 진행할 것.
