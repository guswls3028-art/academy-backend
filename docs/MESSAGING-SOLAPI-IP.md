# 메시지 발송 실패: IP 허용 설정 (솔라피 / 뿌리오 공통)

**증상:** 학생 관리 등에서 “메시지 발송”을 했을 때 발송 내역(메시지 > 발송 내역)에는 실패 건으로 기록되지만, 실제로 문자가 나가지 않음.

**비고에 보이는 에러 예:**
- `(Forbidden', '허용되지 않은 IP(...)로 접근하고 있습니다.')`
- 또는 `IP 미등록` 관련 안내 (워커에서 안내 문구를 붙인 경우)

---

## 원인

솔라피 또는 뿌리오에서 **API 호출 허용 IP**를 제한해 두었을 때, 메시지 워커의 **나가는 IP**가 그 목록에 없으면 요청이 거절됩니다.
→ 발송 요청은 큐에 들어가고, 워커가 API를 호출한 시점에 거절되어 발송 내역에는 “실패”로만 남습니다.

---

## 현재 메시지 워커 IP

메시지 워커는 ASG(Auto Scaling Group) 인스턴스이므로 교체 시 IP가 바뀔 수 있습니다.
현재 IP 확인:
```bash
aws ec2 describe-instances --filters “Name=tag:Name,Values=academy-v1-messaging-worker” “Name=instance-state-name,Values=running” --query “Reservations[].Instances[].PublicIpAddress” --region ap-northeast-2 --output text
```

---

## 해결 방법

### 솔라피(Solapi) 사용 시
1. **솔라피 콘솔** 접속: [https://console.solapi.com](https://console.solapi.com)
2. **설정** 메뉴에서 **허용 IP** (또는 API 보안/IP 제한) 항목을 찾습니다.
3. 위에서 확인한 **메시지 워커 IP**를 **허용 목록에 추가**합니다.

### 뿌리오(Ppurio) 사용 시
1. **뿌리오** 접속: [https://www.ppurio.com](https://www.ppurio.com)
2. **[환경설정] → [접속 IP 관리]** 또는 **[IP 제한 설정]** 메뉴를 찾습니다.
3. 위에서 확인한 **메시지 워커 IP**를 **허용 목록에 추가**합니다.

이후 같은 환경에서의 발송은 정상 처리됩니다.
계속 실패하면 발송 내역의 비고에 기록된 오류 메시지를 확인하세요.
