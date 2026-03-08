# 메시지 발송 사용자 플로우

## 1. 사용자 플로우 (스펙 일치)

### 1.1 발송 진입점

- **학생 관리** (`/admin/students`): 학생 체크박스 선택 → 「메시지 발송」 → 모달
- **강의·수강생** (`/admin/lectures/.../students`): 수강생 선택 → 「메시지 발송」 → 모달
- **출결** (세션 출결·출결 매트릭스): 출결 대상 선택 → 「메시지 발송」 → 모달
- **성적 입력**: 학생 선택 → 「메시지 발송」 → 모달
- **메시지 > 발송** (`/admin/message/send`): 「메시지 발송」 클릭 시 수신자 없이 모달만 오픈 → 학생·강의 등에서 수신자 선택 후 발송 안내
- **직원 홈**: 동일하게 `openSendMessageModal({ studentIds: [] })` 로 모달 오픈 가능

### 1.2 모달 동작 (프론트)

- **수신자**: 학부모 / 학생 체크박스 (둘 다 선택 가능, 최소 1개)
- **발송 유형**: SMS 발송 / 알림톡 발송 체크박스 (둘 다 선택 가능, 최소 1개)
- **내용**: 직접 입력 또는 템플릿 불러오기 → 제목(선택), 본문
- **본문 우선순위**: 템플릿 불러온 뒤 수정한 내용이 있으면 **직접 입력 본문**이 사용됨 (백엔드에서 `raw_body`가 비어 있지 않으면 `raw_body` 사용, 비어 있을 때만 템플릿 본문 사용)

### 1.3 API → SQS → 워커

- **POST /messaging/send/**  
  - `student_ids`, `send_to`("parent"|"student"), `message_mode`("sms"|"alimtalk"|"both"), `raw_body`, `raw_subject`, `template_id`(선택)  
  - 발신번호: 테넌트 `messaging_sender` 필수, 없으면 400  
  - 학생 조회 → 전화번호 추출(send_to 기준) → 본문 치환(`#{student_name_2}`, `#{student_name_3}`, `#{site_link}`) → **enqueue_sms** 호출 시 **sender** 전달
- **enqueue_sms**  
  - SQS 메시지: `tenant_id`, `to`, `text`, `sender`, `message_mode`, `template_id`, `alimtalk_replacements` 등  
  - 등록된 수신번호로 모두 enqueue (수신 제한 없음)
- **워커**  
  - SQS 소비 → 테넌트 정보(발신번호·잔액·PFID) 로드 → 잔액 차감 → Solapi 발송  

---

## 2. 스펙 정합성 정리

| 항목 | 내용 |
|------|------|
| 본문 우선순위 | `raw_body`가 비어 있지 않으면 직접 입력(수정본) 사용, 비어 있을 때만 템플릿 본문 사용 |
| 발신번호 | API에서 `tenant.messaging_sender` 검사 후 `enqueue_sms(..., sender=sender)` 로 전달, 워커는 payload·테넌트·env 순으로 사용 |
| 수신자/발송유형 다중 선택 | 프론트에서 학부모+학생, SMS+알림톡 각각 체크 시 API를 2×2 등으로 여러 번 호출해 각 조합별로 발송 |
| 수신 제한 | 없음. 등록된 학생·학부모 번호로 정상 발송 (SMS 정책은 OWNER_TENANT_ID 기준) |
