# 솔라피 콘솔 확인 가이드 (발신번호 · 잔액 · IP)

메시지 발송 실패 시 솔라피(console.solapi.com)에서 확인할 항목입니다.

---

## 1. 발신번호 등록·인증 상태

1. https://console.solapi.com 접속
2. 왼쪽 메뉴: **메시지** → **발신번호 관리** (또는 **번호 인증**)
3. 확인:
   - 발신번호가 **등록**되어 있는지
   - **인증 완료** 상태인지 (인증 대기/미인증이면 사용 불가)
   - `.env`의 `SOLAPI_SENDER`가 이 번호와 **정확히 일치**하는지

> 발신번호 형식: `01012345678` (하이픈 없이)

---

## 2. 잔액·제한 여부

1. https://console.solapi.com 접속
2. 상단 또는 **충전/잔액** 메뉴
3. 확인:
   - **SMS/LMS 잔액**이 0이 아닌지
   - **제한/블록** 알림이 없는지

---

## 3. IP 허용 목록 (API Key)

1. https://console.solapi.com 접속
2. 왼쪽 메뉴: **개발** → **API Key**
3. 사용 중인 API Key 선택 → **허용 IP 수정**
4. Messaging Worker EC2의 **퍼블릭 IP**가 등록되어 있는지 확인

> EC2 인스턴스가 바뀌면 IP도 바뀝니다. ASG 사용 시 Elastic IP 또는 NAT Gateway로 고정 권장.

---

## 4. MessageNotReceivedError 상세 확인

Worker에서 `[status_code] status_message` 형태로 로그에 남깁니다.  
예: `[E001] 발신번호가 등록되지 않았습니다`

| 코드 예시 | 의미 |
|-----------|------|
| E001 등 | 발신번호 미등록/미인증 |
| E002 등 | 수신번호 형식 오류 |
| 잔액 관련 | 잔액 부족 |

Messaging Worker 로그:
```bash
docker logs academy-messaging-worker --tail 100
```
