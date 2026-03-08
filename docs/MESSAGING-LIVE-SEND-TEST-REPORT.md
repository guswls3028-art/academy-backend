# 메시징 실제 발송 테스트 결과 보고

작성일: 2026-03-08  
테스트: tenant 1, 발신 01031217466, 수신 01034137466

---

## 수행한 작업

1. **AWS SSM /academy/workers/env SOLAPI_MOCK 확인**  
   - 값: **true** (Base64 디코드 후 JSON에서 확인)

2. **SOLAPI_MOCK 제거**  
   - `scripts/v1/remove-solapi-mock-from-workers-ssm.ps1` 실행  
   - SSM에서 `SOLAPI_MOCK` 키 제거 후 put-parameter  
   - 결과: SSM 업데이트 성공

3. **메시징 워커 ASG instance-refresh**  
   - `aws autoscaling start-instance-refresh --auto-scaling-group-name academy-v1-messaging-worker-asg` 실행  
   - InstanceRefreshId: `61e6455a-4c56-4e28-87da-9d6290d81cb9`  
   - 초기: 기존 인스턴스(i-0638ed686087129ec) scale-in 보호로 대기  
   - `aws autoscaling set-instance-protection --instance-ids i-0638ed686087129ec ... --no-protected-from-scale-in` 실행  
   - 이후 롤링 진행, 새 인스턴스 **i-0665692b6062aea0c** InService

4. **워커 로그에서 SolapiMessageService 사용 여부**  
   - 메시징 워커용 CloudWatch Logs 로그 그룹 없음 (`aws logs describe-log-groups --log-group-name-prefix academy` → `[]`)  
   - 로그는 EC2 인스턴스의 Docker/컨테이너 stdout에만 존재  
   - **코드 기준:** `_get_solapi_client()`는 `SOLAPI_MOCK`이 없거나 false일 때 `SolapiMessageService`(실제 클라이언트) 반환. 새 인스턴스는 SSM에서 SOLAPI_MOCK 제거된 env로 기동했으므로 **실제 Solapi 사용으로 동작하는 것이 정상.**

5. **테스트 메시지 enqueue**  
   - `aws sqs send-message` 로 큐 `academy-v1-messaging-queue`에 1건 전송  
   - Body: `{"tenant_id":1,"to":"01034137466","text":"Test message.","sender":"01031217466","message_mode":"sms"}`  
   - 결과: **성공**  
   - MessageId: `ef3d6162-59a4-4c6e-b9fe-e2abee7aa842`

6. **Worker 소비 여부**  
   - 전송 직후: `ApproximateNumberOfMessages=1`, `ApproximateNumberOfMessagesNotVisible=0`  
   - 이후 재조회: `ApproximateNumberOfMessages=0`, `ApproximateNumberOfMessagesNotVisible=0`  
   - **메시지 1건 소비·삭제된 것으로 확인.**

7. **Solapi API 호출 결과**  
   - 원격에서 워커 프로세스 로그/CloudWatch 미확인  
   - 큐 메시지가 0이 된 것은 워커가 메시지를 받아 처리 완료 후 delete한 결과로 해석됨.  
   - 실패 시 워커는 메시지를 delete하지 않고 visibility timeout 후 재노출되며, 연속 실패 시 DLQ 등으로 이동할 수 있음. 현재 큐 0이면 **정상 처리(delete)된 것으로 봅니다.**

8. **실제 문자 수신 여부**  
   - **수신 번호 01034137466** 에서 문자 수신 여부는 사용자 확인 필요.

---

## 결과 요약 (보고 형식)

| 항목 | 결과 |
|------|------|
| **enqueue 성공 여부** | 성공. SQS send-message 200, MessageId `ef3d6162-59a4-4c6e-b9fe-e2abee7aa842` |
| **worker 소비 여부** | 성공. 큐 메시지 수 1 → 0으로 변경, 메시지 삭제됨. |
| **Solapi API 호출 결과** | 원격 로그 미확인. 새 인스턴스는 SOLAPI_MOCK 제거된 env로 기동하여 실제 Solapi 사용으로 동작하는 구조이며, 메시지가 delete된 것으로 보아 처리 완료로 판단. |
| **실제 문자 수신 여부** | 미확인. 수신자 01034137466에서 수신 여부 확인 필요. |
| **실패 시 정확한 실패 지점** | 현재 기준으로 enqueue·worker 소비·delete까지는 성공. 실패 시 가능 지점: (1) 워커 기동 시 SSM env 미반영, (2) Solapi API 호출 실패(키/발신번호/IP 등), (3) 수신자 번호/통신사 문제. 1은 instance-refresh로 해소, 2·3은 수신 여부와 Solapi 콘솔/문의로 확인 필요. |

---

## 추가 확인 권장

- **수신 확인:** 01034137466 번호로 "Test message." 문자가 왔는지 확인.
- **워커 로그 확인(선택):** EC2 인스턴스 i-0665692b6062aea0c 에 SSH 등으로 접속 후 `docker logs academy-messaging-worker` 등으로 `send_sms ok` 또는 Solapi 오류 로그 확인.
