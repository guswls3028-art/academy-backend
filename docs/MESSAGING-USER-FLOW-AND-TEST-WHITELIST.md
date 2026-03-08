# 메시지 발송 사용자 플로우 및 테스트 수신 제한

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
  - 테스트용 화이트리스트 설정 시: 화이트리스트에 없는 수신번호는 enqueue 스킵
- **워커**  
  - SQS 소비 → 테넌트 정보(발신번호·잔액·PFID) 로드 → 잔액 차감 → Solapi 발송  
  - 테스트용 화이트리스트 설정 시: 화이트리스트에 없는 수신번호는 발송 스킵 후 메시지 삭제

---

## 2. 테스트용 수신번호 화이트리스트 (실제 학생 발송 방지)

실제로 학생에게 문자가 나가지 않도록, **특정 수신번호로만** 발송되게 할 때 사용합니다.

### 2.1 설정 방법

- **환경 변수**  
  - `MESSAGING_TEST_RECIPIENT_WHITELIST`  
  - 값: 쉼표 구분 수신번호 (예: `01034137466`)  
  - 공백·하이픈은 무시됨

- **API 서버**  
  - Django 설정 `MESSAGING_TEST_RECIPIENT_WHITELIST` (base.py에서 `os.getenv("MESSAGING_TEST_RECIPIENT_WHITELIST", "")` 로드)  
  - 배포 환경에서는 SSM `/academy/api/env` 등에 해당 키가 들어가야 함 (값이 비어 있으면 화이트리스트 미적용)

- **워커**  
  - EC2/컨테이너 env에서 `MESSAGING_TEST_RECIPIENT_WHITELIST` 로드 (SSM `/academy/workers/env` 에 넣으면 됨)

### 2.2 동작

- **API**  
  - 화이트리스트가 설정되어 있으면, 학생별로 조회한 전화번호가 화이트리스트에 있을 때만 `enqueue_sms` 호출  
  - 그 외는 스킵하고 `skipped_whitelist` 카운트 증가  
  - 응답에 `skipped_whitelist` 포함

- **enqueue_sms (services)**  
  - `MESSAGING_TEST_RECIPIENT_WHITELIST`가 설정되어 있고, `to`가 화이트리스트에 없으면 enqueue 하지 않고 `False` 반환

- **워커**  
  - `MESSAGING_TEST_RECIPIENT_WHITELIST`가 설정되어 있고, 메시지의 `to`가 화이트리스트에 없으면 발송하지 않고 메시지 삭제 후 다음 메시지 처리

### 2.3 허용 번호만 테스트할 때

- API·워커 env에  
  `MESSAGING_TEST_RECIPIENT_WHITELIST=01034137466`  
  설정 후, 해당 번호가 포함된 수신자만 실제 발송·enqueue됩니다.  
  다른 번호는 API/워커에서 스킵되므로 **실제 학생에게는 문자가 나가지 않습니다.**

---

## 3. 스펙 정합성 정리

| 항목 | 내용 |
|------|------|
| 본문 우선순위 | `raw_body`가 비어 있지 않으면 직접 입력(수정본) 사용, 비어 있을 때만 템플릿 본문 사용 |
| 발신번호 | API에서 `tenant.messaging_sender` 검사 후 `enqueue_sms(..., sender=sender)` 로 전달, 워커는 payload·테넌트·env 순으로 사용 |
| 수신자/발송유형 다중 선택 | 프론트에서 학부모+학생, SMS+알림톡 각각 체크 시 API를 2×2 등으로 여러 번 호출해 각 조합별로 발송 |
| 테스트 수신 제한 | `MESSAGING_TEST_RECIPIENT_WHITELIST` 설정 시 지정한 번호로만 enqueue·실제 발송 |
