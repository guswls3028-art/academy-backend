# 메시징 서비스 완료 요약 (프로덕션)

## 1. 테스트 로직 제거 (완료)

- **화이트리스트 제거**
  - `apps/support/messaging/views.py`: 수신번호 화이트리스트 분기 및 `skipped_whitelist` 제거
  - `apps/support/messaging/services.py`: `enqueue_sms()` 내 화이트리스트 검사 제거
  - `apps/worker/messaging_worker/sqs_main.py`: 워커 측 화이트리스트 검사 제거
  - `apps/api/config/settings/base.py`: `MESSAGING_TEST_RECIPIENT_WHITELIST` 설정 제거
  - **SSM:** `scripts/v1/update-messaging-whitelist-ssm.ps1`를 빈 값으로 실행해 `/academy/api/env`, `/academy/workers/env`에서 해당 키 제거(cleared) 완료
- **프론트**
  - `SendMessageResponse`에서 `skipped_whitelist` 제거, 발송 모달에서 해당 안내 제거
- **결과:** 등록된 학생·학부모 번호로 제한 없이 정상 발송 (SMS 정책은 OWNER_TENANT_ID 기준 유지).

---

## 2. 1번 테넌트 · 01034137466 검증용 학생 생성 및 발송

### 2.1 관리 명령 (서버/로컬)

- **명령:** `python manage.py messaging_create_student_and_send_verify`
  - 1번 테넌트에 학부모 전화 `01034137466`인 학생 생성(없으면 생성, 있으면 재사용)
  - 해당 번호로 검증용 SMS 1건 enqueue → 워커가 Solapi로 발송
- **옵션:** `--tenant=1 --parent-phone=01034137466 --name=메시지검증용` (기본값)
- **실행 위치:** API 서버에서 컨테이너 기동 후  
  `docker exec academy-api python manage.py messaging_create_student_and_send_verify --tenant=1 --parent-phone=01034137466`  
  또는 SSM Run Command로: `scripts/v1/run-messaging-verify-send.ps1` (API 인스턴스에 `academy-api` 컨테이너가 떠 있어야 함).

### 2.2 프론트에서 검증 발송

1. 실제 프론트 도메인(1번 테넌트) 로그인
2. **학생 추가:** 학부모 전화 `01034137466`인 학생 1명 등록 (또는 이미 있으면 생략)
3. 학생 목록에서 해당 학생 선택 → 「메시지 발송」 → 수신자 학부모, 발송 유형 SMS → 내용 입력 → 발송
4. **01034137466** 단말에서 문자 수신 확인
5. (선택) 메시지 > 발송 내역에서 로그 확인

---

## 3. 메시지 도메인 · 파이프라인

### 3.1 구조

- **API:** POST `/api/v1/messaging/send/` → 학생 조회 → 본문 치환 → `enqueue_sms()` → SQS
- **워커:** SQS Long Polling → 잔액 차감 → Solapi SMS/알림톡 발송 → NotificationLog 기록
- **프론트:** 학생/강의/출결/성적 등에서 「메시지 발송」 → `SendMessageModal` → sendMessage API

### 3.2 인프라

- **SQS:** academy-v1-messaging-queue (DLQ 포함)
- **ASG:** academy-v1-api-asg, academy-v1-messaging-worker-asg
- **SSM:** /academy/api/env, /academy/workers/env (화이트리스트 키 제거됨)

### 3.3 문제 시 확인

- **403 "문자(SMS) 발송은 내 테넌트에서만 가능합니다."**  
  → `OWNER_TENANT_ID`와 현재 테넌트 불일치. `apps/support/messaging/policy.py`의 `can_send_sms`
- **400 발신번호 없음**  
  → 테넌트 `messaging_sender` 미설정. 메시지 > 설정에서 발신번호 등록·인증
- **문자 미수신**  
  → 워커 로그, Solapi 콘솔 발송 이력, 잔액·발신번호·수신번호 확인

---

## 4. 정리

- 메시징 기능은 **테스트용 수신 제한 없이** 프로덕션 동작합니다.
- 01034137466 검증은 위 2.1(관리 명령) 또는 2.2(프론트 발송)로 수행하면 됩니다.
