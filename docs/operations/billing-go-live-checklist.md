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

계약가 예외: `limglish`, `ymath`는 플랜 정가가 아니라 월 공급가액
150,000원, 부가세 15,000원, 실제 결제 합계 165,000원으로 청구한다.
`Program.resolve_monthly_price()`와 billing migration `0041_set_ymath_limglish_contract_price`가 SSOT다.
`monthly_price`는 하위 호환용 공급가액 필드이며 VAT 포함 금액이 아니다.
UI/API 소비자는 `monthly_supply_amount`, `monthly_tax_amount`,
`monthly_total_amount`, `monthly_price_includes_tax`를 사용한다. 계약가는
프로모션이 아니므로 `billing_price_policy=contract_override`, `is_promo=false`다.
계약 유형과 `billing_price_integrity`/`is_billing_price_ready`는 인증된 staff 및
플랫폼 관리자 응답에만 노출하며, 불일치 상태에서는 새 인보이스 생성을 차단한다.
`python manage.py audit_billing_fields`도 이 상태를
`contract_price_mismatch` 수동 조치 항목으로 보고한다(자동 금액 변경 없음).

구독 유예기간 SSOT는 `BILLING_GRACE_PERIOD_DAYS`(기본 7일)다. 유예 상태의
실제 접근 종료일은 `service_access_expires_at`/`grace_expires_at`이며,
`process_billing`이 active → grace → expired 전이를 수행한다.

인보이스는 `SCHEDULED → PENDING → PAID/FAILED` 상태기계를 따른다.
`INVOICE_REQUEST`는 due date에 `PENDING`으로 전환된 뒤에만 수동 입금 확인할
수 있고, 입금 확인과 `PaymentTransaction(provider=manual, SUCCESS)` 기록은
원자적으로 처리된다.

결제 완료 알림톡의 provider SID는 2026-07-08 실등록 감사 기준 미등록이다.
승인 SID가 다시 등록되기 전에는 preview/send 모두 발송 불가로 fail-closed해야
하며 운영 상태 API에서 `payment_complete`를 unavailable trigger로 명시한다.

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

# 2) Phase A: 키는 주입하되 자동결제/암호문 writer는 아직 비활성
#    (라이브 전환 시에는 test_ 접두사를 live_로 변경)
jq '. + {
  "TOSS_PAYMENTS_SECRET_KEY": "test_sk_XXXXXXXXXXXX",
  "TOSS_PAYMENTS_CLIENT_KEY": "test_ck_XXXXXXXXXXXX",
  "TOSS_WEBHOOK_SECRET":     "whsec_XXXXXXXXXXXX",
  "TOSS_AUTO_BILLING_ENABLED": "false",
  "BILLING_KEY_ENCRYPTION_WRITE_ENABLED": "false",
  "BILLING_KEY_ENCRYPTION_PRIMARY_KEY": "FERNET_URLSAFE_BASE64_32_BYTE_KEY",
  "BILLING_KEY_ENCRYPTION_FALLBACK_KEYS": ""
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
  --preferences '{"MinHealthyPercentage": 100, "InstanceWarmup": 300}'

# 5) Phase A 완료 후 검증 (5~10분 후)
aws ssm send-command \
  --region ap-northeast-2 \
  --targets "Key=tag:Name,Values=academy-v1-api" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["docker exec academy-api python -c \"from django.conf import settings; print(len(settings.TOSS_PAYMENTS_SECRET_KEY), settings.TOSS_AUTO_BILLING_ENABLED, settings.BILLING_KEY_ENCRYPTION_WRITE_ENABLED, len(settings.BILLING_KEY_ENCRYPTION_PRIMARY_KEY))\""]'
```

Phase A 검증 출력이 `N(>0) False False K(>0)` 형태여야 한다. Fernet KEK는
Toss/Django SECRET_KEY와 별개로 생성하고 비밀 저장소에서 관리한다.

전 API가 호환 코드의 digest-pinned 이미지임을 검증한 뒤에만 Phase B를 실행한다.

```bash
# 6) 최신 Phase A env를 다시 읽고 writer/자동결제를 활성화
aws ssm get-parameter \
  --region ap-northeast-2 \
  --name /academy/api/env \
  --with-decryption \
  --query 'Parameter.Value' \
  --output text > /tmp/api_env_phase_a_live.json

jq '. + {
  "BILLING_KEY_ENCRYPTION_WRITE_ENABLED": "true",
  "TOSS_AUTO_BILLING_ENABLED": "true"
}' /tmp/api_env_phase_a_live.json > /tmp/api_env_phase_b.json

aws ssm put-parameter \
  --region ap-northeast-2 \
  --name /academy/api/env \
  --type SecureString \
  --value "$(cat /tmp/api_env_phase_b.json)" \
  --overwrite

aws autoscaling start-instance-refresh \
  --region ap-northeast-2 \
  --auto-scaling-group-name academy-v1-api-asg \
  --preferences '{"MinHealthyPercentage": 100, "InstanceWarmup": 300}'
```

Phase B refresh 후 같은 검증 명령의 출력이 `N(>0) True True K(>0)`이고
`python manage.py audit_billing_fields --strict`가 성공해야 한다.

#### 빌링키 저장 암호화의 2단계 전환

`BILLING_KEY_ENCRYPTION_WRITE_ENABLED`는 첫 배포와 동시에 켜면 안 된다. 새
바이너리는 평문과 암호문을 모두 읽지만, 이전 바이너리는 암호문을 읽지 못하므로
rolling refresh 도중 자동결제가 실패할 수 있다.

1. **Phase A:** 새 코드를 `BILLING_KEY_ENCRYPTION_WRITE_ENABLED=false`로 배포한다.
2. ASG의 모든 InService API가 새 digest-pinned 이미지이고 migration이 완료됐는지
   `scripts/v1/run-deploy-verification.ps1`로 확인한다.
3. 전용 `BILLING_KEY_ENCRYPTION_PRIMARY_KEY`를 주입한 뒤에만 write flag를 `true`로
   바꾸고 API instance refresh를 한 번 더 수행한다. 운영 설정은 primary key가 없으면
   기동 자체를 거부한다.
4. `python manage.py audit_billing_fields --strict`를 실행해
   `plaintext_billing_key`와 `undecryptable_billing_key`가 모두 0건인지 확인한다. 이
   감사에는 실제 빌링키가 출력되지 않는다.

2026-07-13 운영 읽기 전용 감사에서는 BillingKey가 전체/활성 모두 0건이므로
기존 평문 backfill 대상은 없다. 향후 flag 활성화 시 평문 행이 발견되면 해당 키를
공급사에서 재발급하거나, 별도 검증된 re-encryption 절차로 전환하기 전까지 자동결제를
열지 않는다. 암호문 저장을 시작한 뒤에는 구버전 이미지로의 API rollback을 금지하고
새 이미지로 roll-forward한다.

결제 KEK는 Django SECRET_KEY와 독립적으로 순환한다. 기존 primary가 `K1`, 새 키가
`K2`일 때 rolling fleet 양쪽이 서로의 암호문을 읽게 하려면 아래 3단계를 반드시
지킨다.

1. primary는 `K1`로 유지하고 fallback에 `K2`를 추가해 전 API fleet를 refresh한다.
   이 단계의 writer는 계속 `K1`만 사용한다.
2. primary를 `K2`, fallback을 `K1`로 바꾸고 다시 전 fleet를 refresh한다. 이전/새
   인스턴스 모두 `K1`과 `K2`를 읽을 수 있다.
3. 아래 명령으로 모든 행을 `K2`로 다시 감싸고 strict audit 성공 후에만 fallback
   `K1`을 제거해 마지막 refresh를 한다.

명령은 빌링키 원문을 출력하지 않으며 하나라도 복호화할 수 없으면 쓰기 전에 전량
중단한다.

```bash
python manage.py rotate_billing_key_encryption
python manage.py rotate_billing_key_encryption --execute --confirm-live
python manage.py audit_billing_fields --strict
```

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
