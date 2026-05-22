# Billing Go-Live Checklist

결제 시스템(Toss Payments 자동결제) 실사용 오픈 전 사용자(오너) 액션 목록.
**코드/인프라 레벨에서 자동화 가능한 모든 작업은 이미 완료됨.**
여기 남은 항목은 외부 계약/대시보드 로그인/키 주입이 필요해서 AI가 대리할 수 없다.

작성일: 2026-04-20
관련 커밋: `7d47d176` (`feat(billing): 자동결제(Phase D) + Toss 웹훅 완성`)

---

## ✅ 이미 완료된 것 (참고용)

| 항목 | 상태 | 비고 |
|------|------|------|
| Phase D 자동결제 실행 로직 | ✅ 배포 완료 | `payment_service.execute_auto_payment` |
| Toss 웹훅 엔드포인트 | ✅ 배포 완료 | `POST /api/v1/billing/webhooks/toss/` |
| HMAC-SHA256 서명 검증 | ✅ 배포 완료 | `verify_webhook_signature` |
| 테넌트 미들웨어 bypass | ✅ 배포 완료 | `/api/v1/billing/webhooks/` prefix |
| 인보이스 생성/상태 전이 | ✅ 기존 구현됨 | `invoice_service` |
| 일일 배치 스케줄러 | ✅ AWS CLI로 생성 완료 | EventBridge `academy-v1-process-billing` (매일 15:05 UTC = 00:05 KST) |
| IAM role (EB → SSM) | ✅ AWS CLI로 생성 완료 | `academy-v1-eventbridge-ssm-billing-role` |
| 테스트 148건 | ✅ 전부 통과 | 22개 신규 + 126 회귀 |

현재 상태: **TOSS_AUTO_BILLING_ENABLED=False** (휴면 상태. 배치가 돌아도 실제 결제 안 함.)

---

## 🔴 사용자 직접 액션 (1~4 순서대로)

### 1. Toss Payments 계약 체결

**왜 내가 못하나:** 사업자 계약. 사업자 등록증/대표자 신분증/정산 계좌 등 필요.

**무엇을 하나:**
1. https://www.tosspayments.com/ 접속 → 가맹점 가입
2. 상품: **일반결제 + 빌링(자동결제)** 선택 (빌링 별도 심사)
3. 정산 계좌, 사업자등록증, 대표자 신분증 제출
4. 심사 승인 후 "내 상점 > 상점 정보" 이동
5. 3종 키 확보 (테스트/라이브 각각):
   - **Secret Key** (서버용, 백엔드에서만 사용) — 예: `test_sk_...` / `live_sk_...`
   - **Client Key** (프론트 SDK용) — 예: `test_ck_...` / `live_ck_...`
   - **Webhook Secret** (웹훅 서명 검증용) — "내 상점 > 개발자 센터 > 웹훅" 메뉴

**참고:** 테스트 키로 먼저 시작 권장. 라이브 전환 시 SSM만 재주입.

---

### 2. SSM `/academy/api/env`에 Toss 환경변수 주입

**왜 내가 못하나:** 키 값 자체를 1단계 이후에만 얻을 수 있음.

**무엇을 하나:**

```bash
# 1) 현재 env 덤프
aws ssm get-parameter \
  --region ap-northeast-2 \
  --name /academy/api/env \
  --with-decryption \
  --query 'Parameter.Value' \
  --output text > /tmp/api_env_current.json

# 2) jq로 4개 키 추가 (라이브 전환 시에는 test_ 접두사를 live_로 변경)
jq '. + {
  "TOSS_PAYMENTS_SECRET_KEY": "test_sk_XXXXXXXXXXXX",
  "TOSS_PAYMENTS_CLIENT_KEY": "test_ck_XXXXXXXXXXXX",
  "TOSS_WEBHOOK_SECRET":     "whsec_XXXXXXXXXXXX",
  "TOSS_AUTO_BILLING_ENABLED": "true"
}' /tmp/api_env_current.json > /tmp/api_env_new.json

# 3) SSM 업데이트
aws ssm put-parameter \
  --region ap-northeast-2 \
  --name /academy/api/env \
  --type SecureString \
  --value "$(cat /tmp/api_env_new.json)" \
  --overwrite

# 4) ASG instance refresh (새 env 반영)
aws autoscaling start-instance-refresh \
  --region ap-northeast-2 \
  --auto-scaling-group-name academy-v1-api-asg \
  --preferences '{"MinHealthyPercentage": 100, "InstanceWarmup": 120}'

# 5) 완료 후 검증 (5~10분 후)
aws ssm send-command \
  --region ap-northeast-2 \
  --targets "Key=tag:Name,Values=academy-v1-api" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["docker exec academy-api python -c \"from django.conf import settings; print(len(settings.TOSS_PAYMENTS_SECRET_KEY), settings.TOSS_AUTO_BILLING_ENABLED)\""]'
```

검증 출력이 `N(>0) True` 형태면 성공.

**주의:**
- 비밀번호/키에 bash 특수문자(`$`, `&`, 백틱) 없는지 확인 — core.md §7 규칙.
- ASG refresh 약 3~5분 소요 (MinHealthy=100%라 무중단).

---

### 3. Toss 대시보드에 웹훅 URL 등록

**왜 내가 못하나:** Toss 가맹점 계정 로그인 필요.

**무엇을 하나:**
1. Toss 가맹점 관리자 → **개발자 센터 > 웹훅** 메뉴
2. "웹훅 추가" 클릭
3. URL: `https://api.hakwonplus.com/api/v1/billing/webhooks/toss/` (끝 슬래시 필수)
4. 이벤트 구독: **Payment.Status.Changed** (결제 상태 변경) 체크
   - 추가 선택 가능: `Payment.Canceled` (부분/전체 취소 동기화용)
5. 저장
6. Toss 화면에서 "테스트 전송" 클릭
7. 확인:
   ```bash
   # CloudWatch Logs에서 academy-api 로그 필터
   aws logs tail /aws/ec2/academy-v1-api --since 5m --region ap-northeast-2 --filter-pattern "Toss webhook"
   ```
   `Toss webhook received: event=PAYMENT_STATUS_CHANGED` 라인이 보이면 성공.

---

### 4. 실서비스 오픈 테스트

**누적 테스트 시나리오 (limglish 또는 테스트 테넌트)**

#### 4a. 카드 등록 (테스트 키로)
1. 원장 계정으로 `https://app.hakwonplus.com/admin/settings` 접속
2. 결제수단 섹션 > "카드 등록"
3. Toss 테스트 카드(4330-1234-1234-1234 같은 가상카드)로 등록
4. 백엔드 확인:
   ```bash
   aws ssm send-command --region ap-northeast-2 --targets "Key=tag:Name,Values=academy-v1-api" \
     --document-name AWS-RunShellScript \
     --parameters 'commands=["docker exec academy-api python manage.py shell -c \"from apps.billing.models import BillingKey; print(list(BillingKey.objects.filter(is_active=True).values(\\\"tenant__code\\\",\\\"card_company\\\",\\\"card_number_masked\\\")))\""]'
   ```

#### 4b. 자동결제 수동 트리거
```bash
# 테스트용: next_billing_at을 오늘로 당기고 process_billing 실행
aws ssm send-command --region ap-northeast-2 --targets "Key=tag:Name,Values=academy-v1-api" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["docker exec academy-api python manage.py process_billing"]'

# 결과 확인
aws ssm list-command-invocations --region ap-northeast-2 --command-id <위_명령_ID> --details \
  --query "CommandInvocations[].CommandPlugins[0].Output" --output text
```
출력에 `[PAID]` 라인이 있고 invoice.status=PAID, BillingKey로 차지된 금액이 Toss 대시보드에 보여야 함.

#### 4c. 라이브 전환
- 2번 단계에서 Secret/Client Key를 `live_sk_...`/`live_ck_...`로 교체
- Webhook Secret은 라이브 전용 값 (Toss 대시보드에서 라이브 모드로 전환 후 재발급)
- ASG instance refresh 1회 더 실행

---

## 🟡 선택 작업 (Terraform 정합성 — 당장 불필요)

현재 AWS 인프라는 Terraform state 없이 관리되고 있어, 새 리소스를 AWS CLI로 직접 생성했다.
Terraform 관리 체계로 편입하려면:

1. Terraform state S3 버킷 + DynamoDB 락 테이블 생성
2. `terraform import` 로 기존 모든 리소스를 state에 편입
3. `backend "s3"` 블록 활성화 (versions.tf)

`billing_schedule.tf` 파일은 참고용으로 남아 있다. 지금 `terraform apply`하지 말 것 (기존 ASG/ALB/Batch 리소스 충돌).

---

## 참고

- Toss 공식 문서: https://docs.tosspayments.com/
- 빌링 API: https://docs.tosspayments.com/reference/billing
- 웹훅: https://docs.tosspayments.com/guides/webhook
- 로컬 테스트:
  ```bash
  cd backend && source .venv/Scripts/activate
  python manage.py test apps.billing.tests.test_payment_service apps.billing.tests.test_webhook
  ```
