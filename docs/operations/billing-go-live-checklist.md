# Billing Go-Live Checklist

결제 시스템(Toss Payments 자동결제) 실사용 오픈 전 사용자(오너) 액션 목록.
**코드/인프라 레벨에서 자동화 가능한 모든 작업은 이미 완료됨.**
여기 남은 항목은 외부 계약/대시보드 로그인/키 주입이 필요해서 AI가 대리할 수 없다.

작성일: 2026-04-20
최종 운영 확인: 2026-07-27
관련 커밋: `7d47d176` (`feat(billing): 자동결제(Phase D) + Toss 웹훅 완성`)

---

## ✅ 이미 완료된 것 (참고용)

| 항목 | 상태 | 비고 |
|------|------|------|
| Phase D 자동결제 실행 로직 | ✅ 배포 완료 | `payment_service.execute_auto_payment` |
| Toss 웹훅 엔드포인트 | ✅ 배포 완료 | `POST /api/v1/billing/webhooks/toss/` |
| 웹훅 공급사 재조회 검증 | ✅ 배포 완료 | payload는 힌트로만 사용하고 주문번호로 Toss Payment를 재조회 |
| 테넌트 미들웨어 bypass | ✅ 배포 완료 | `/api/v1/billing/webhooks/` prefix |
| 인보이스 생성/상태 전이 | ✅ 기존 구현됨 | `invoice_service` |
| 일일 배치 스케줄러 | ✅ AWS CLI로 생성 완료 | EventBridge `academy-v1-process-billing` (매일 15:05 UTC = 00:05 KST) |
| IAM role (EB → SSM) | ✅ AWS CLI로 생성 완료 | `academy-v1-eventbridge-ssm-billing-role` |
| 테스트 148건 | ✅ 전부 통과 | 22개 신규 + 126 회귀 |

현재 상태: **TOSS_AUTO_BILLING_ENABLED=False** (휴면 상태. 배치가 돌아도 실제 결제 안 함.)
운영 이용료 수납은 `BILLING_BANK_TRANSFER_ENABLED=true`인 계좌이체 경로만
사용한다. 카드 등록·결제 UI는 Toss 카드 결제 계약을 시작할 때까지 숨기며,
기존 카드 데이터와 서버 호환 경로는 삭제하지 않는다.
운영 SSM에는 전용 빌링키 암호화 KEK와 암호문 writer가 준비돼 있지만,
Toss 서버/클라이언트 키는 아직 없다. 일반 결제 웹훅에는 서명 secret이
제공되지 않으므로 별도 `TOSS_WEBHOOK_SECRET`을 만들거나 요구하지 않는다.

모든 운영 테넌트는 단일 `all` 요금제로 전체 기능을 사용한다. 일반 신규 가입가는
월 공급가액 180,000원, 부가가치세 18,000원(10%), 실제 결제 합계
198,000원이다. 2026년 7월 25일까지 생성된 기존 계약은 종전 스냅샷을
자동 인상하지 않는다.
`Program.PLAN_PRICES`, `Program.calculate_monthly_amounts()`와 core migrations
`0045_unify_subscription_plan`(rolling 호환 schema) 및
`0046_apply_single_subscription_plan`(data 수렴 + 최종 schema)이 SSOT다.
`monthly_price`는 학원별 월 공급가 계약 스냅샷이며 VAT 포함 금액이 아니다.
UI/API 소비자는 `monthly_supply_amount`, `monthly_tax_amount`,
`monthly_total_amount`, `monthly_price_includes_tax`를 사용한다. 일반가는
10% VAT를 적용하고, 종전 및 8월 특별가의 145,000원 계약은 승인된 고정
세액 14,000원을 유지한다.

2026-08-01부터 2026-08-31까지 KST 기준으로 생성된 `Program`은
`Program.created_at`을 가입 시점 SSOT로 삼아 평생 가격 보장 코호트로 판정한다.
해당 학원은 일반가 198,000원 대신 공급가 145,000원, 부가가치세
14,000원, 합계 159,000원을 유지한다. API는 이 코호트에
`billing_price_policy=promotion`, `has_lifetime_price_guarantee=true`,
`price_guarantee_code=august_2026_lifetime`을 반환한다. 그 외 학원은
`billing_price_policy=single`이다. 향후 기본가 인상은 8월 보장 코호트를
제외한 행만 명시적 rolling migration으로 수렴해야 하며, 임의 일괄 갱신은
금지한다. 비가격 `Program` 설정 저장도 기존 비보장 계약가를 자동 변경하지
않는다. 새 기본가와 기존 비보장 계약가가 다른 전환 구간에는
`billing_price_integrity`가 청구를 차단하며, 검증된 rolling migration이 해당
행을 새 기본가로 수렴시킨 뒤에만 청구를 재개한다. 2026년 7월 26일 이후
비8월 신규 계약은 공급가 180,000원으로 생성된다. 인보이스와 MRR은
`monthly_price` 스냅샷을 기준으로 계산한다.

`billing_price_integrity`/`is_billing_price_ready`는 인증된 staff 및
플랫폼 관리자 응답에만 노출하며, 불일치 상태에서는 새 인보이스 생성을 차단한다.
로그인 전 `/api/v1/core/program/` bootstrap은 브랜딩과 기능 설정만 반환하고
계약가, 구독 상태, 구독 기간을 노출하지 않는다.
`python manage.py audit_billing_fields`도 이 상태를
`single_plan_mismatch` 또는 `single_price_mismatch` 수동 조치 항목으로 보고한다.
전환 시 아직 발행되지 않은 `SCHEDULED` 인보이스만 해당 Program 계약
스냅샷으로 수렴한다. 이미 발행된 `PENDING`/`FAILED`/`OVERDUE` 인보이스는 당시 계약의
회계 스냅샷이므로 변경하지 않는다.

단일 요금제는 수납 관리, AI 채점, 매치업, 저장공간 200GB를 포함한 전체 기능을
제공한다. `feature_flags`는 학원별 운영 모드를 위한 설정일 뿐 결제 등급이나
기능 잠금으로 사용하지 않는다.

구독 유예기간 SSOT는 `BILLING_GRACE_PERIOD_DAYS`(기본 7일)다. 유예 상태의
실제 접근 종료일은 `service_access_expires_at`/`grace_expires_at`이며,
`process_billing`이 active → grace → expired 전이를 수행한다.
장기간 실행이 누락된 테넌트는 한 번의 실제 배치에서 두 전이를 연속 적용해
`expired`로 수렴한다. `--dry-run`도 DB를 변경하지 않으면서 같은 전체 전이
체인을 출력해야 한다. `audit_billing_fields --strict`는 만료일이 지난 `active`
상태와 유예 종료일이 지난 `grace` 상태를 운영 오류로 판정한다.

명시적으로 승인된 무기한 이용 테넌트는 운영 SSM `/academy/api/env`의
`BILLING_EXEMPT_TENANT_IDS`로 관리한다. 예외 테넌트는 만료일·다음 결제일 없이
`Program.is_subscription_active=True`이며 402 접근 제한, 정기 청구서 생성과
구독 만료 전이 대상에서 제외된다. 기간을 임의의 먼 미래 날짜로 만들거나 코드의
기본 예외값을 운영 계약 기록으로 사용하지 않는다.

예외 추가·제거는 기존 ID를 보존한 단일 키 안전 업데이트로 수행하고 API ASG를
무중단 교체한다. 적용 후 실제 런타임 예외 목록, 대상 Program의 상태·만료일·다음
결제일·`is_subscription_active`, `audit_billing_fields --tenant <code>`를 읽어
확인한다. 예외를 제거할 때는 먼저 승인된 계약 기간과 다음 결제일을 설정해 접근
공백이나 즉시 만료가 생기지 않게 한다.

인보이스는 `SCHEDULED → PENDING → PAID/FAILED` 상태기계를 따른다.
`INVOICE_REQUEST`는 due date에 `PENDING`으로 전환된 뒤에만 수동 입금 확인할
수 있고, 입금 확인과 `PaymentTransaction(provider=manual, SUCCESS)` 기록은
원자적으로 처리된다.

### 현재 운영 수납 경로: 계좌이체

`TOSS_AUTO_BILLING_ENABLED=false`인 동안에도 B2B 프로그램 이용료를 받을 수
있도록 아래 운영 경로를 사용한다. 계좌 정보는 소스·문서·프론트 빌드에
하드코딩하지 않고 `/academy/api/env`의 SSM 환경값으로만 관리한다.

1. `/academy/api/env`에 아래 값을 저장한 뒤 API instance refresh를 수행한다.
   - `BILLING_BANK_TRANSFER_ENABLED=true`
   - `BILLING_BANK_NAME`
   - `BILLING_BANK_ACCOUNT_NUMBER`
   - `BILLING_BANK_ACCOUNT_HOLDER`
2. 학원 오너가 `결제 / 구독 → 계좌이체 결제`에서 계좌이체 청구를 활성화한다.
   처리 중인 카드 청구가 없을 때만 예정 카드 청구를 계좌이체 청구로 바꾼다.
3. 오너가 표시된 청구 총액을 이체한 뒤 입금자명·이체 시각을 신고한다.
   세금계산서를 원하면 사업자등록번호와 수신 이메일을 함께 저장한다.
4. 플랫폼 superuser가 개발자 결제 콘솔의 `입금 신고` 탭에서 실제 통장 내역과
   금액을 대조한 뒤 `입금 확인` 또는 `반려`를 선택한다.
5. 입금 확인 시에만 청구서가 `PAID`가 되고 구독과 수납 장부에 반영된다.
6. 세금계산서 요청 건은 `READY` 대기열에서 홈택스로 실제 발행한 뒤 국세청
   승인번호를 `발행 완료 기록`에 입력한다.

프론트 현재 계약은
[`frontend/docs/USER-GUIDE-ADMIN.md`](../../../frontend/docs/USER-GUIDE-ADMIN.md#14-4-구독결제)에
기록한다. 결제 설정 화면은 계좌이체 청구·입금 신고만 노출하고 카드 섹션은
렌더링하지 않는다. 내부 `AUTO_CARD` 상태가 남아 있는 기존 테넌트도 화면에서는
`계좌이체 선택 전`으로 안내하며, 오너가 계좌이체를 선택할 때만 예정 청구서를
`INVOICE_REQUEST`로 전환한다.

고객의 입금 신고만으로 결제 완료 처리하지 않는다. `READY`도 홈택스 발행 완료가
아니며, 승인번호가 기록된 `ISSUED`만 발행 완료다. 운영 계좌 값은 소스나 문서에
기록하지 않고 SSM 환경값으로만 관리한다.

결제 완료 알림톡의 provider SID는 2026-07-08 실등록 감사 기준 미등록이다.
승인 SID가 다시 등록되기 전에는 preview/send 모두 발송 불가로 fail-closed해야
하며 운영 상태 API에서 `payment_complete`를 unavailable trigger로 명시한다.

---

## 🔴 사용자 직접 액션 (1~5 순서대로)

### 1. Toss Payments 계약 체결

**왜 내가 못하나:** 사업자 계약. 사업자 등록증/대표자 신분증/정산 계좌 등 필요.

**무엇을 하나:**
1. https://www.tosspayments.com/ 접속 → 가맹점 가입
2. 상품: **일반결제 + 빌링(자동결제)** 선택 (빌링 별도 심사)
3. 정산 계좌, 사업자등록증, 대표자 신분증 제출
4. 심사 승인 후 "내 상점 > 상점 정보" 이동
5. 자동결제 MID의 API 개별 연동 키 2종 확보 (테스트/라이브 각각):
   - **Secret Key** (서버용, 백엔드에서만 사용) — 예: `test_sk_...` / `live_sk_...`
   - **Client Key** (프론트 SDK용) — 예: `test_ck_...` / `live_ck_...`
   - 두 키는 반드시 같은 자동결제 MID에서 발급된 한 세트여야 한다.

**참고:** 테스트 키로 먼저 시작 권장. 라이브 전환 시 SSM만 재주입.

---

### 2. SSM `/academy/api/env`에 Toss 환경변수 주입

**왜 내가 못하나:** 키 값 자체를 1단계 이후에만 얻을 수 있음.

키를 로컬 보안 파일로 받은 뒤에는 값을 명령행이나 로그에 노출하지 않는 전용
스크립트를 우선 사용한다.

```powershell
# Phase A: 테스트 키 저장 + 카드 등록 검증. 실제 자동결제는 계속 OFF.
pwsh scripts/v1/set-toss-billing.ps1 `
  -Mode Test `
  -ClientKeyFile C:\secure\toss-client-key.txt `
  -SecretKeyFile C:\secure\toss-secret-key.txt `
  -RefreshInstances

# Phase B: 라이브 키 저장 + 실제 자동결제 ON.
pwsh scripts/v1/set-toss-billing.ps1 `
  -Mode Live `
  -ClientKeyFile C:\secure\toss-client-key.txt `
  -SecretKeyFile C:\secure\toss-secret-key.txt `
  -EnableAutoBilling `
  -RefreshInstances
```

운영에서 `TOSS_AUTO_BILLING_ENABLED=true`는 `live_ck_`/`live_sk_` 키 한 세트,
암호문 writer, primary KEK가 모두 준비된 경우에만 서버가 기동된다. 테스트 키로
전역 자동결제를 켜는 것은 부팅 단계에서 차단한다.

**무엇을 하나:**

```bash
# 1) 현재 env 덤프
aws ssm get-parameter \
  --region ap-northeast-2 \
  --name /academy/api/env \
  --with-decryption \
  --query 'Parameter.Value' \
  --output text > /tmp/api_env_current.json

# 2) Phase A: 키는 주입하되 자동결제 승인은 아직 비활성
#    기존 BILLING_KEY_ENCRYPTION_* 값은 유지한다.
#    (라이브 전환 시에는 test_ 접두사를 live_로 변경)
jq '. + {
  "TOSS_PAYMENTS_SECRET_KEY": "test_sk_XXXXXXXXXXXX",
  "TOSS_PAYMENTS_CLIENT_KEY": "test_ck_XXXXXXXXXXXX",
  "TOSS_AUTO_BILLING_ENABLED": "false"
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

Phase A 검증 출력이 `N(>0) False True K(>0)` 형태여야 한다. Fernet KEK는
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

#### 빌링키 저장 암호화 상태와 향후 순환

2026-07-27 운영은 `BILLING_KEY_ENCRYPTION_WRITE_ENABLED=true`이고 전용
primary KEK가 주입된 상태다. Toss 키를 넣기 위해 이 flag나 KEK를 끄거나
교체하지 않는다. 아래 2단계 전환 설명은 구버전 fleet에서 암호문 writer를
처음 켜는 경우에만 적용한다.

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
4. 이벤트 구독:
   - **Payment.Status.Changed** (결제 상태 변경)
   - **Billing.Deleted** / `BILLING_DELETED` (빌링키 삭제)
5. 저장
6. Toss 화면에서 "테스트 전송" 클릭
7. 확인:
   ```bash
   # CloudWatch Logs에서 academy-api 로그 필터
   aws logs tail /aws/ec2/academy-v1-api --since 5m --region ap-northeast-2 --filter-pattern "Toss webhook"
   ```
   `Toss webhook received: event=PAYMENT_STATUS_CHANGED` 라인이 보이면 성공.

일반 결제 웹훅에는 HMAC 서명 header가 없으므로 서버는 웹훅 body를 직접
신뢰하지 않고, 로컬에 존재하는 `orderId`만 받아 Toss 결제 조회 API로
상태·금액·결제키를 다시 검증한다. 또한 자동결제 승인이 완료될 때는
`PAYMENT_STATUS_CHANGED`가 오지 않으므로 최초 성공 판정은 자동결제 API의
동기 응답이 정본이고, 타임아웃 등 결과 미확정 건은
`reconcile_processing_payments`로 조회한다.

---

### 4. 실서비스 오픈 테스트

**누적 테스트 시나리오 (Tenant 9999 전용 테스트 데이터만 사용)**

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
- ASG instance refresh 1회 더 실행

라이브 전환 전에는 테스트/라이브 키를 섞지 않는다. 카드 등록 준비 API도
서버키·클라이언트키가 없거나 환경 prefix가 서로 다르면 503으로 차단한다.

---

### 5. 전자세금계산서 발행사와 정책 확정

Toss Payments는 카드 자동결제를 담당하며 전자세금계산서 국세청 전송은
별도 발행사 연동 범위다. 현재 `BusinessProfile`, `BankTransferNotice`,
`TaxInvoiceIssue`와 홈택스 수동 발행 대기열은 연결되어 있지만 외부 발행
API는 연결하지 않았다.

구현 전 오너가 확정할 항목:

1. 발행사: 팝빌/바로빌 등 계약한 전자세금계산서 API 공급사
2. 발행 기준: `INVOICE_REQUEST`만 발행하고 카드 자동결제에는 발행하지 않을지
3. 발행 시점: 청구 시점의 `청구` 발행 또는 입금 확인 후 `영수` 발행
4. 공급자 정보: 사업자등록번호, 상호, 대표자, 주소, 업태, 종목,
   담당자 이름/이메일/연락처
5. 공동인증서 등록 및 국세청 전송 설정
6. 발행사 API 자격증명과 테스트/운영 환경

발급시기와 중복 증빙 정책은 세무대리인 확인값을 정본으로 사용한다. 이 결정
전에는 외부 발행 API를 붙이지 않는다. 수동 경로에서는 홈택스 실제 발행 후
승인번호를 확인한 운영자만 `TaxInvoiceIssue`를 `ISSUED`로 올린다.

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
- 웹훅 이벤트: https://docs.tosspayments.com/reference/using-api/webhook-events
- 로컬 테스트:
  ```bash
  cd backend && source .venv/Scripts/activate
  python manage.py test apps.billing.tests.test_payment_service apps.billing.tests.test_webhook
  ```
