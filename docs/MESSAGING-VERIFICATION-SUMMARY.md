# 메시지 발송 검증 요약 (화이트리스트·도메인·통합)

## 1. 화이트리스트 변경 (완료)

- **요청:** 01035023313 제거, 01034137466만 허용
- **조치:**
  - `apps/api/config/settings/base.py`: 주석 예시를 `"01034137466"` 만 사용하도록 수정
  - `docs/MESSAGING-USER-FLOW-AND-TEST-WHITELIST.md`: 예시·설명을 01034137466 단일 번호 기준으로 수정
  - **SSM:** `scripts/v1/update-messaging-whitelist-ssm.ps1` 실행으로 `/academy/api/env`, `/academy/workers/env` 에 `MESSAGING_TEST_RECIPIENT_WHITELIST=01034137466` 설정 완료
  - **API:** `scripts/v1/refresh-api-env.ps1` 실행으로 기동 중인 API 인스턴스에 SSM env 반영 및 컨테이너 재시작 완료
- **워커:** SSM에는 이미 01034137466만 저장됨. 기동 중인 워커는 부팅 시점 env를 쓰므로, **즉시 반영하려면** `scripts/v1/restart-workers.ps1`(또는 메시징 워커 ASG instance-refresh) 실행 필요. 미실행 시 다음 교체/재기동 시 새 값 적용됨.
- **추가:** `update-workers-env-sqs.ps1` 에서 API env 복사 시 `MESSAGING_TEST_RECIPIENT_WHITELIST` 도 포함하도록 수정 (이후 SQS 갱신 시 워커 env에 자동 반영).

---

## 2. 프론트 메시지 발송 기능 (실제 도메인 동작)

### 2.1 진입점 및 라우트

| 위치 | 경로 | 동작 |
|------|------|------|
| 메시지 도메인 | `/admin/message` | 템플릿 저장, **발송**, 자동발송, 발송 내역, 설정 |
| 메시지 > 발송 | `/admin/message/send` | 「메시지 발송」 클릭 시 모달 오픈 (수신자 없이도 모달만 열림) |
| 학생 | `/admin/students` | 학생 선택 후 「메시지 발송」 → 모달 |
| 강의·수강생 | `/admin/lectures/:id` | 수강생 선택 후 「메시지 발송」 → 모달 |
| 출결 | 세션 출결/출결 매트릭스 | 선택 후 「메시지 발송」 → 모달 |
| 성적 입력 | 세션 성적 페이지 | 학생 선택 후 「메시지 발송」 → 모달 |
| 직원 홈 | `/admin/staff` 등 | 동일 모달 사용 가능 |

### 2.2 수정 사항

- **AsyncStatusBar:** 메시지 작업 클릭 시 이동 경로를 `/admin/messages` → **`/admin/message`** 로 수정 (실제 라우트와 일치).

### 2.3 API 플로우 (정상)

- **POST /api/v1/messaging/send/**  
  - Body: `student_ids`, `send_to`(parent|student), `message_mode`(sms|alimtalk|both), `raw_body`, `raw_subject`, `template_id`(선택)  
  - 발신번호: 테넌트 `messaging_sender` 필수  
  - 응답: `enqueued`, `skipped_no_phone`, `skipped_whitelist`  
- **화이트리스트:** API·워커 모두 `MESSAGING_TEST_RECIPIENT_WHITELIST=01034137466` 적용 시, **01034137466** 수신만 enqueue·실제 발송됨.

### 2.4 실제 도메인에서의 확인 방법

1. **실제 프론트 도메인** (학원별 admin URL) 접속 후 로그인
2. 학생 목록에서 **학부모 전화번호가 01034137466인 학생** 1명 선택 (또는 해당 번호로 테스트 학생 등록)
3. 「메시지 발송」 → 수신자 학부모, 발송 유형 SMS 선택 → 내용 입력 → 발송
4. **01034137466** 단말에서 문자 수신 여부 확인
5. (선택) 메시지 > 발송 내역에서 해당 건 로그 확인

---

## 3. 메시지 도메인 내 문자 발송 통합 상태

### 3.1 통합 구조

- **백엔드:** `apps/support/messaging/`  
  - `views.py`: SendMessageView (POST send/), MessagingInfoView, 템플릿·자동발송·로그·설정  
  - `services.py`: `enqueue_sms()` — 화이트리스트 적용 후 SQS enqueue  
  - `urls.py`: `/messaging/` 하위 info, send, templates, auto-send, log 등
- **워커:** `apps/worker/messaging_worker/sqs_main.py` — SQS 소비 후 동일 화이트리스트 적용, Solapi 발송
- **프론트:** `src/features/messages/`  
  - `api/messages.api.ts`: sendMessage → POST `/messaging/send/`  
  - `components/SendMessageModal.tsx`: 수신자·발송유형·내용 입력 후 sendMessage 호출  
  - 메시지 도메인: 발송, 템플릿 저장, 자동발송, 발송 내역, 설정이 한 레이아웃(MessageLayout) 아래 통합

### 3.2 정상 동작 확인된 사항

- API send 엔드포인트와 요청/응답 스키마 일치
- 화이트리스트는 API(enqueue 단계)와 워커(발송 단계) 양쪽에서 적용
- 프론트 모달은 학부모/학생, SMS/알림톡 조합별로 API 호출
- 메시지 메뉴 경로(`/admin/message`)와 사이드/AsyncStatusBar 이동 경로 일치하도록 수정 완료

### 3.3 문제 발생 시 확인 포인트

- **403 "문자(SMS) 발송은 내 테넌트에서만 가능합니다."**  
  → OWNER_TENANT_ID와 현재 테넌트 불일치. 정책: `apps/support/messaging/policy.py` 의 `can_send_sms`
- **400 발신번호 없음**  
  → 해당 테넌트의 발신번호(messaging_sender) 미설정. 메시지 > 설정에서 발신번호 등록
- **enqueued=0, skipped_whitelist>0**  
  → 수신 번호가 화이트리스트(현재 01034137466)에 없음. 해당 번호로만 실제 발송됨
- **문자 미수신**  
  → 워커 로그(Solapi 응답), Solapi 콘솔 발송 이력, 워커 env에 `MESSAGING_TEST_RECIPIENT_WHITELIST=01034137466` 적용 여부 확인

---

## 4. 01034137466 실제 발송 테스트

- **조건:** API·워커에 화이트리스트 01034137466 적용 후, 해당 번호를 수신자로 하는 요청만 enqueue·발송됨.
- **방법:** 위 2.4 절에 따라 실제 프론트 도메인에서 학부모 01034137466인 학생 선택 후 SMS 발송 → 수신 확인.
- **워커 미반영 시:** `pwsh scripts/v1/restart-workers.ps1`(또는 메시징 워커 ASG만 instance-refresh) 실행 후 동일 절차로 재테스트.
