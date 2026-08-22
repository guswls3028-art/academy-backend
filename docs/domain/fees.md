# 학원 수납·청구

`apps/domains/fees/`는 플랫폼 구독 결제와 분리된 학원별 학생 수납 도메인이다.
원장·관리자는 비목, 학생별 비용, 월 청구서와 수납을 관리하고 학생·학부모는
선택된 학생의 청구·납부 내역을 조회한다.

## 진입점과 소유 경계

- 관리 API: `/api/v1/fees/templates/`, `student-fees/`, `invoices/`,
  `payments/`, `dashboard/`
- 학생·학부모 조회 API: `/api/v1/student/fees/invoices/`, `payments/`
- 모델과 상태 전이: `apps/domains/fees/models.py`,
  `apps/domains/fees/services/__init__.py`
- 화면 계약: `frontend/src/shared/api/contracts/fees.ts`

모든 관리 작업은 요청에서 확정된 테넌트의 원장·관리자만 수행한다. 학생·학부모
조회는 로그인 사용자와 `X-Student-Id`로 확정된 연결 학생만 허용하며 다른 학생이나
다른 테넌트로 대체하지 않는다.

## 청구와 재청구

월 청구 생성은 활성 `StudentFee`를 학생별로 합산해 `StudentInvoice`와
`InvoiceItem` 스냅샷을 만든다. 같은 테넌트·학생·연월에는 취소되지 않은 청구서가
최대 하나만 존재한다. 청구서를 취소해도 기존 행과 항목은 감사 이력으로 보존하며,
같은 연월을 다시 생성하면 새 청구번호의 활성 청구서를 만든다. 취소 이력이 활성
청구의 재생성을 막아서는 안 된다.

수납 기록은 청구서를 잠근 뒤 성공 수납 합계로 `paid_amount`와 상태를 재계산한다.
멱등성 키 또는 짧은 중복 방지 구간으로 재전송을 차단하고, 잔액 초과·취소 청구서·
다른 테넌트 청구서 수납은 실패한다. 성공 수납이 남아 있는 청구서는 먼저 수납을
취소해야 청구서 취소가 가능하다.

## 조회와 실패 처리

대시보드는 취소되지 않은 청구서만 합산한다. 청구 총액·수납 총액·상태별 건수·전체
건수는 하나의 청구 집계 쿼리로 계산하고, 비목별 합계는 별도 항목 집계 쿼리로
계산한다. 빈 달은 모든 합계와 건수를 0으로 반환한다.

월 생성은 학생별 성공·건너뜀·오류 건수를 반환한다. 동시 생성 시 DB의 활성 청구
부분 유일성 제약이 마지막 방어선이며, 이미 다른 요청이 만든 활성 청구는 중복 생성
대신 건너뜀으로 처리한다.

## 집중 검증

```powershell
$env:DJANGO_SETTINGS_MODULE='apps.api.config.settings.test'
python manage.py test apps.domains.fees.tests.test_payment_lifecycle
python manage.py makemigrations --check --dry-run
```

PostgreSQL 수납 경합은 별도로 다음 테스트를 실행한다.

```powershell
$env:DJANGO_SETTINGS_MODULE='apps.api.config.settings.test_pg'
python manage.py test apps.domains.fees.tests.test_payment_concurrency_pg
```
