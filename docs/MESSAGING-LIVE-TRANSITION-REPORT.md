# 문자 기능 실제 운영 전환 보고서

작성일: 2026-03-08  
목표: 문자 기능을 실제 운영 가능한 상태로 전환 · 1건 실발송 검증 · 관측/로그 보강안 제시.

---

## 1. 확인된 사실

- **SSM `/academy/workers/env`:** `SOLAPI_MOCK`, `DEBUG` 키 **없음**. (스크립트 `check-workers-env-mock-debug.ps1` 실행 결과.)
- **Mock 분기 조건 (코드):** `apps/worker/messaging_worker/sqs_main.py`의 `_get_solapi_client()`는 `os.environ.get("SOLAPI_MOCK","").lower() in ("true","1","yes")` 또는 `os.environ.get("DEBUG", ...)` 일 때만 Mock 사용. 키가 없으면 **실제 SolapiMessageService** 사용.
- **워커 env 반영 시점:** `scripts/v1/resources/worker_userdata.ps1` — EC2 부팅 시 `aws ssm get-parameter --name /academy/workers/env` 로 **한 번** 읽어 `/opt/workers.env` 생성 후 컨테이너 `--env-file` 전달.
- **instance-refresh:** 실행함. InstanceRefreshId `0c4e6b7a-aff9-486d-a202-0e47a8e776dc`. 구 인스턴스 i-0665692b6062aea0c scale-in 보호 해제 후 롤링 진행, **새 인스턴스 i-05f73e79ee6362e92** InService 확인.
- **새 인스턴스 기동 시점:** refresh 중 기동 → 부팅 시점의 SSM은 **이미 SOLAPI_MOCK/DEBUG 제거된 상태**였음. 따라서 해당 인스턴스의 워커 프로세스는 **live 모드**로 기동한 것으로 판단 가능.
- **tenant 1 SMS 허용:** `apps/support/messaging/policy.py`의 `can_send_sms(tenant_id)` — `OWNER_TENANT_ID` 기본값 1, tenant 1이면 True.
- **발신번호:** 워커 SSM의 `SOLAPI_SENDER` = **01012345678**. 메시지 body에 `sender`를 넣으면 해당 값으로 발송됨. 이번 테스트에서는 body에 `sender: "01031217466"` 지정.
- **큐/워커 경로:** `MESSAGING_SQS_QUEUE_NAME=academy-v1-messaging-queue`, 큐 URL 존재 확인. 워커 ASG가 해당 큐 구독.
- **enqueue:** SQS `send-message` 1건 성공. MessageId `e23ff78d-a405-4d90-94b5-a1b66426b7e8`.
- **worker consume:** 발송 후 약 25초 내 큐 `ApproximateNumberOfMessages=0`, `ApproximateNumberOfMessagesNotVisible=0` 확인 → 메시지 1건 소비·삭제됨.
- **Solapi 콘솔·수신:** 원격에서 Solapi 대시보드 접근 및 수신 단말 확인은 미수행. 운영자가 [console.solapi.com](https://console.solapi.com)에서 발신 01031217466 / 수신 01034137466 / 해당 시각 발송 이력 및 수신 번호 01034137466 도착 여부를 확인하면 됨.

---

## 2. 실제 수정/실행한 항목

- **SSM 수정:** 없음. (이미 SOLAPI_MOCK/DEBUG 키 없음.)
- **스크립트 추가:**  
  - `scripts/v1/check-workers-env-mock-debug.ps1` — 워커 SSM에서 SOLAPI_MOCK/DEBUG 존재 여부 확인.  
  - `scripts/v1/check-workers-sender-queue.ps1` — SOLAPI_SENDER, MESSAGING_SQS_QUEUE_NAME, OWNER_TENANT_ID 확인.
- **instance-refresh:**  
  - `aws autoscaling set-instance-protection --instance-ids i-0665692b6062aea0c --no-protected-from-scale-in`  
  - `aws autoscaling start-instance-refresh --auto-scaling-group-name academy-v1-messaging-worker-asg`  
  - 새 인스턴스 i-05f73e79ee6362e92 InService 확인.
- **실발송 1건:**  
  - `aws sqs send-message --queue-url ... --message-body file://scripts/v1/tmp-message-body.json`  
  - Body: `{"tenant_id":1,"to":"01034137466","text":"Academy live test 1","sender":"01031217466","message_mode":"sms"}`  
  - (작업 후 `tmp-message-body.json` 삭제.)

---

## 3. 현재 워커가 live인지 여부

- **결론: live.**  
  - SSM에 SOLAPI_MOCK/DEBUG 없음.  
  - 현재 동작 중인 메시징 워커 인스턴스(i-05f73e79ee6362e92)는 refresh로 기동한 인스턴스이며, 부팅 시 위 SSM을 읽어 `/opt/workers.env`를 생성함.  
  - 따라서 해당 인스턴스의 워커는 Mock 분기를 타지 않고 **실제 SolapiMessageService**를 사용하는 것으로 판단함.

---

## 4. 테스트 발송 성공/실패

- **enqueue:** 성공.  
- **worker consume:** 성공 (큐 메시지 0으로 소비·삭제 확인).  
- **worker가 mock이 아닌 실제 provider 사용:** 코드·SSM·인스턴스 기동 시점 기준 **실제 provider 사용으로 판단**. (원격 워커 stdout/CloudWatch 미확인.)  
- **Solapi 콘솔에 해당 발송 이력 존재:** 운영자 확인 필요.  
- **수신 단말(01034137466) 도착:** 운영자 확인 필요.

**종합:** enqueue·consume까지는 성공. Solapi 콘솔 이력 및 수신 여부는 운영자가 확인 후 최종 판정.

---

## 5. 실패 시 정확한 실패 지점

- 현재 기준으로 **enqueue·worker 소비** 단계는 성공.  
- 만약 수신이 안 되었다면 가능 지점:  
  - Solapi API 호출 실패(발신번호 01031217466 미등록·미승인, IP 미허용, 한도 등) → 워커는 실패 시 메시지를 delete하지 않으나, 이번에는 delete됨. 따라서 API가 성공을 반환했거나, 예외 전에 delete되는 경로는 없음.  
  - 통신사/수신자 측 미배달(번호 이동, 수신 거부 등) → Solapi 콘솔에서 “성공”으로 보일 수 있음.  
- **실제 실패가 있었다면:** Solapi 콘솔 해당 건 상태·에러 메시지, 및 필요 시 워커 로그(EC2 인스턴스 `docker logs`)로 실패 지점 확인 필요.

---

## 6. 서비스 운영 가능 상태로 보려면 남은 필수 작업

- **운영자 확인:**  
  - Solapi 콘솔에서 발신 01031217466 → 수신 01034137466, 해당 시각 발송 건 존재·상태 확인.  
  - 수신 번호 01034137466에서 문자 수신 여부 확인.  
- **발신번호 정책:** 01031217466을 기본 발신번호로 쓰려면 API SSM 및 워커 SSM에 `SOLAPI_SENDER=01031217466` 설정 후 `update-workers-env-sqs.ps1` 실행 및 메시징 워커 instance-refresh. (현재 워커 SSM 기본 발신번호는 01012345678.)

---

## 7. 선택 보강 작업

### 7.1 워커 로그 중앙 수집(CloudWatch)

- **현재:** 메시징 워커는 EC2 Docker 컨테이너로 동작하며, CloudWatch Logs 로그 그룹 없음. 로그는 인스턴스의 컨테이너 stdout에만 존재.
- **최소 방안:**  
  - CloudWatch 로그 그룹 생성(예: `/aws/ec2/academy-messaging-worker`).  
  - Launch Template UserData의 `docker run`에 `--log-driver awslogs --log-opt awslogs-group=... --log-opt awslogs-region=...` 추가.  
  - EC2 인스턴스 역할(academy-ec2-role)에 `logs:CreateLogStream`, `logs:PutLogEvents` 등 CloudWatch Logs 권한 보유 확인.  
  - 적용 시 LT 버전 업데이트 후 메시징 워커 ASG instance-refresh.

### 7.2 발송 이력 최소 저장

- **현재:** `NotificationLog` 모델(tenant, sent_at, success, amount_deducted, recipient_summary, template_summary, failure_reason) 존재. 워커가 성공/실패 시 `create_notification_log()` 호출함(sqs_main.py).
- **보강 시:** DB 컬럼 또는 별도 테이블에 `provider_group_id`(Solapi group_id), `mock_used`(bool) 추가하면 Solapi 이력 매칭·Mock 여부 추적에 유리함. 기존 코드 구조를 크게 바꾸지 않는 범위에서 선택 적용.

### 7.3 관리 앱에 발송 모드(mock/live) 및 최근 테스트 결과 표시

- **API:** GET `/messaging/info/` 등에 `mock_or_live`(또는 `solapi_mock`) — API 서버의 env `SOLAPI_MOCK`/`DEBUG` 유무로 판단해 반환. (API는 enqueue만 하고 실제 발송은 워커가 하므로, “워커 기준” 모드는 API에서 직접 알 수 없음. SSM 또는 별도 헬스/설정 API로 워커 env를 노출할 경우 가능.)
- **관리 앱:** 위 값이 있으면 “테스트 모드(Mock)” / “실제 발송(Live)” 표시. 최근 테스트 결과는 `NotificationLog` 최근 1건 또는 별도 “마지막 검증 시각” 엔드포인트로 표시 가능.

---

**관련 문서**

- `docs/MESSAGING-SMS-NOT-RECEIVED-ROOT-CAUSE-REPORT.md`
- `docs/MESSAGING-LIVE-SEND-TEST-REPORT.md`
- `docs/MESSAGING-POLICY-VERIFICATION-REPORT.md`
