# 문자 미수신 원인 분석 보고서 (01034137466)

작성일: 2026-03-08  
대상: 수신 번호 **01034137466** — 테스트 발송 후 문자가 도착하지 않음.

---

## 1. 요약

- **결론:** 메시지를 처리한 워커가 **SOLAPI_MOCK(또는 Mock 모드)** 으로 동작했을 가능성이 가장 높다. Mock 모드에서는 실제 Solapi API를 호출하지 않고 로그만 남기며 `status: "ok"` 를 반환하므로, 큐 메시지는 정상적으로 삭제되지만 **실제 문자는 발송되지 않는다.**
- **근거:** 워커는 `result.get("status") == "ok"` 일 때만 SQS 메시지를 delete한다. 메시지가 큐에서 삭제된 것은 워커가 “성공”으로 처리했다는 의미이며, 이는 (1) Mock 사용 시 항상 성공 반환, 또는 (2) 실제 Solapi 호출 성공 두 경우뿐이다. 문자가 도착하지 않았다면 **(1) Mock으로 처리되었을 가능성이 유력**하다.

---

## 2. 코드·동작 기준 정리

### 2.1 워커의 삭제 조건

- **파일:** `apps/worker/messaging_worker/sqs_main.py`
- **동작:** `send_one_sms()` / `send_one_alimtalk()` 반환값이 `result.get("status") == "ok"` 일 때만 `queue_client.delete_message(...)` 호출.
- **실패 시:** `status != "ok"` 이면 delete 하지 않음 → visibility timeout 후 메시지 재노출 또는 DLQ 이동 가능.

따라서 **메시지가 큐에서 삭제되었다 = 워커가 “성공”으로 판단했다** 는 뜻이다.

### 2.2 Mock 사용 시 동작

- **파일:** `apps/support/messaging/solapi_mock.py`  
  - `MockSolapiMessageService.send()` 는 실제 API 호출 없이 `logger.info("[MockSolapi] 발송 스킵 (실제 API 미호출)...")` 만 남기고, `registered_success=count` 인 Mock 응답 객체를 반환.
- **파일:** `apps/worker/messaging_worker/sqs_main.py`  
  - `send_one_sms()` 는 `client.send(message)` 후 `group_id` 만 추출하고, **예외가 없으면** `{"status": "ok", "group_id": group_id}` 반환.
- **결과:** Mock 사용 시에는 **항상 `status: "ok"`** → 워커가 메시지를 delete하고, **실제 문자는 한 통도 나가지 않음.**

### 2.3 Mock이 선택되는 조건

- **파일:** `apps/worker/messaging_worker/sqs_main.py` — `_get_solapi_client(cfg)`  
  - `os.environ.get("SOLAPI_MOCK", "").lower() in ("true", "1", "yes")` **또는**  
  - `os.environ.get("DEBUG", "").lower() in ("true", "1", "yes")`  
  이면 `MockSolapiMessageService` 를 반환한다.
- **env 출처:** 메시징 워커 EC2는 부팅 시 UserData가 `aws ssm get-parameter --name /academy/workers/env` 로 SSM을 **한 번** 읽어 `/opt/workers.env` 를 만들고, 컨테이너에 `--env-file` 로 전달한다. **이미 떠 있는 인스턴스는 SSM을 다시 읽지 않는다.**

---

## 3. 가능 원인 시나리오

### 3.1 (가장 유력) 메시지를 처리한 인스턴스가 Mock 모드였음

- **시나리오 A — 구 인스턴스가 메시지를 소비:**  
  - instance-refresh 시 scale-in 보호 해제 후, **구 인스턴스가 종료되기 전**에 테스트 메시지가 enqueue 되었고, **아직 살아 있던 구 인스턴스**가 SQS를 폴링해 메시지를 가져갔을 수 있다.  
  - 구 인스턴스는 부팅 시점에 SSM에서 **SOLAPI_MOCK=true** 인 env로 기동했으므로, **Mock 모드로 처리 → 실제 발송 없음 → delete 만 수행.**

- **시나리오 B — 새 인스턴스가 “갱신 전” SSM을 읽음:**  
  - 새 인스턴스 기동 시점에 SSM get-parameter 가 **갱신 전** 값을 반환했을 가능성(캐시/타이밍).  
  - 이 경우에도 새 인스턴스의 `/opt/workers.env` 에 SOLAPI_MOCK=true 가 들어가 Mock 모드로 동작.

- **시나리오 C — SSM 갱신 방식 이슈:**  
  - `remove-solapi-mock-from-workers-ssm.ps1` 은 JSON에서 `SOLAPI_MOCK` 키를 제거한 뒤 put-parameter 로 덮어쓴다.  
  - PowerShell `ConvertFrom-Json` / `ConvertTo-Json` 의 키 대소문자·속성 제거 방식에 따라, 특정 환경에서 키가 남거나 다른 키로 저장되었을 가능성은 이론상 존재한다. (현재 스크립트는 표준적으로는 제거에 유리한 형태.)

### 3.2 (가능) 실제 Solapi 호출은 성공했으나 미배달

- Solapi API가 200/성공을 반환했지만, **통신사·수신자 측** 사유(번호 이동, 수신 거부, 일시적 장애 등)로 문자가 실제 단말에 도달하지 않았을 수 있다.
- 이 경우에도 워커는 `status == "ok"` 로 delete 하며, **Solapi 콘솔 발송 이력**에는 “성공”으로 남을 수 있다.

### 3.3 (가능하나 현재 증거와 맞지 않음) Solapi API 실패

- Solapi 호출이 실패하면 `send_one_sms()` 에서 예외 또는 `{"status": "error", "reason": "..."}` 반환 → 워커는 delete 하지 않는다.
- 그런데 **메시지가 큐에서 삭제된 것이 확인**되었으므로, “API 실패로 delete 안 함” 시나리오는 **성립하지 않는다.**  
  즉, **처리한 워커 입장에서는 “성공”이었다** (Mock 성공 또는 실제 API 성공).

---

## 4. 검증 권장 조치

아래 순서로 확인하면 원인을 좁히는 데 도움이 된다.

| 순서 | 조치 | 목적 |
|------|------|------|
| 1 | **현재 워커 인스턴스의 실제 env 확인** | Mock 여부 확정 |
| 2 | **Solapi 콘솔 발송 이력 조회** | 실제 API 호출 발생 여부 확인 |
| 3 | **동일 조건 재발송 테스트** | 현재 환경에서 수신 가능 여부 확인 |
| 4 | (선택) **워커 로그 수집** | 해당 MessageId 기준 send_sms ok / Mock 로그 확인 |

### 4.1 현재 워커 env 확인

- 메시징 워커 ASG의 **현재 인스턴스**에 접속 가능하다면:
  - 컨테이너 내부: `docker exec <container> env` 또는 `docker exec <container> cat /opt/workers.env` (호스트에 마운트된 경우 호스트에서 `cat /opt/workers.env`)
  - **확인할 항목:** `SOLAPI_MOCK`, `DEBUG` 존재 여부 및 값.
- SSM만으로 확인:
  - `aws ssm get-parameter --name /academy/workers/env --with-decryption ...` 후 Base64 디코딩한 JSON에 **SOLAPI_MOCK** 키가 있는지 확인.  
  - 있으면 아직 Mock 가능성이 있으며, **현재 기동 중인 인스턴스가 언제 기동했는지**(SSM 갱신 이전/이후)와 함께 보면 좋다.

### 4.2 Solapi 콘솔

- [Solapi 콘솔](https://console.solapi.com) (또는 사용 중인 Solapi 대시보드)에서:
  - 해당 일시 전후로 **발신 01031217466 → 수신 01034137466** 인 발송 건이 있는지,
  - 있다면 **상태(성공/실패/미배달 등)** 와 실패 사유(있다면) 확인.
- **발송 이력이 전혀 없다면** → Mock으로만 처리된 것으로 보는 것이 타당하다.
- **발송 이력이 “성공”으로 있다면** → 3.2(통신사/수신자 측 미배달) 가능성을 검토.

### 4.3 재발송 테스트

- **현재** SSM에 SOLAPI_MOCK이 없고, **현재 떠 있는 메시징 워커**가 그 SSM으로 기동된 인스턴스라면:
  - 동일 조건(발신 01031217466, 수신 01034137466, message_mode sms)으로 1건 다시 enqueue 한 뒤,
  - 수신 여부와 Solapi 콘솔 이력을 함께 확인하면 “지금 환경에서는 실제 발송이 되는지”를 바로 검증할 수 있다.
- 재발송 전에 **현재 워커 env에 SOLAPI_MOCK/DEBUG가 없는지** 먼저 확인하는 것을 권장한다.

### 4.4 (선택) 워커 로그

- 메시징 워커용 CloudWatch Logs 그룹이 없으므로, EC2 인스턴스(또는 호스트)에 접속해 Docker 컨테이너 로그를 확인할 수 있다면:
  - 해당 시각·MessageId 주변에서 `[MockSolapi] 발송 스킵` 또는 `send_sms ok to=0103****` 로그가 있는지 보면, Mock 처리 여부를 바로 구분할 수 있다.

---

## 5. 정리

- **01034137466에 문자가 안 온 가장 유력한 원인:**  
  **메시지를 처리한 워커가 SOLAPI_MOCK(또는 DEBUG) 설정으로 Mock 모드로 동작했고, 실제 Solapi API를 호출하지 않은 상태에서 “성공”으로 처리해 메시지를 delete 한 경우.**

- **근거:**  
  - 워커는 성공 시에만 delete 하므로, “큐에서 삭제됨” = “워커가 성공으로 판단함”.  
  - 성공으로 판단되는 경우는 (1) Mock 사용 시의 가짜 성공, (2) 실제 Solapi 성공뿐인데, 문자가 안 왔으므로 (1)이 더 타당함.

- **다음 단계:**  
  1) 현재 워커 env에서 SOLAPI_MOCK/DEBUG 제거 여부 확인,  
  2) Solapi 콘솔에서 해당 발신/수신/일시로 발송 이력 유무·상태 확인,  
  3) 필요 시 Mock이 제거된 상태에서 동일 조건으로 재발송 테스트 및 수신 확인.

---

**관련 문서**

- `docs/MESSAGING-LIVE-SEND-TEST-REPORT.md` — 실제 발송 테스트 수행 내역
- `docs/MESSAGING-POLICY-VERIFICATION-REPORT.md` — 메시징 정책·SSM·Mock 상태 검증
