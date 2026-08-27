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
청구의 재생성을 막아서는 안 된다. `ONE_TIME` 비목은 학생 비용 할당 행을 잠근 뒤
활성 청구 이력을 다시 확인하므로 서로 다른 월 생성 요청이 겹쳐도 한 번만 청구한다.
직접 생성, 일괄 배정, 수강 등록 자동 배정과 월 청구 생성은 같은 활성 학생 행을 ID
오름차순으로 먼저 잠그는 직렬화 경계를 사용한다. 청구 생성은 그 잠금 뒤 학생의
전체 `StudentFee`와 연결 `FeeTemplate`을 다시 잠그고 활성 여부, 시작·종료월, 청구
주기, template 금액, 개별 조정액과 할인을 최신 행으로 평가한다. 따라서 후보 조회
뒤 잠금 전에 커밋된 새 비용도 같은 청구에 포함되고, 잠금 뒤 시작한 새 배정은 청구
트랜잭션이 끝날 때까지 대기한다. 학생 비용의 학생 식별자는 생성 후 변경할 수 없고,
직접 생성은 학생 잠금 안에서 동일 비목을 다시 확인해 경합 중복도 400으로 거부한다.
잠금 후 soft-delete된 학생은 배정을 거부하며 월 청구 후보와 생성에서도 제외한다.
학생 비용의 시작·종료월은 유효한 `YYYY-MM`만 허용하고 종료월이 시작월보다 빠르면
저장을 거부한다. 비활성 비용을 일괄 재할당하면 기존 행을 재활성화하고 종료월을
비운다. 검증 도입 전 저장된 잘못된 월 문자열은 다른 유효 비용의 생성을 중단시키지
않고 오류 목록에 보고한 뒤 해당 비용만 제외한다. 이 변경은 해당 레거시 행을 자동
보정하거나 삭제하지 않는다.

수납 기록은 청구서를 잠근 뒤 성공 수납 합계로 `paid_amount`와 상태를 재계산한다.
멱등성 키 또는 짧은 중복 방지 구간으로 재전송을 차단하고, 잔액 초과·취소 청구서·
다른 테넌트 청구서 수납은 실패한다. 성공 수납이 남아 있는 청구서는 먼저 수납을
취소해야 청구서 취소가 가능하다. 같은 멱등성 키의 성공 수납은 금액과 수단까지
일치할 때만 기존 결과를 반환하며, 다른 요청 정보나 이미 취소·환불된 키의 재사용은
도메인 오류로 응답한다. 영수증 메모는 DB 계약과 동일하게 최대 300자다.

미완납 청구서는 납부기한이 지난 즉시 `OVERDUE`다. 부분납 기록, 수납 취소, 납부기한
수정처럼 합계나 기한이 바뀌는 모든 경로에서 상태를 같은 트랜잭션 안에서 다시
계산한다. 완납은 기한과 무관하게 `PAID`, 미래 기한의 부분납은 `PARTIAL`, 미납은
`PENDING`이다. 청구서는 월 생성 API로만 만들며 목록 엔드포인트의 직접 `POST`는
405로 거부하고 committed OpenAPI에서도 생성 operation을 노출하지 않는다.
OpenAPI는 월 생성의 `GenerateInvoices` 요청과 생성·건너뜀·오류 응답, 수납 생성의
`RecordPayment` 요청, 학생 비용 일괄 배정의 ID 요청과 집계 응답, 청구서 PATCH의
수정 요청과 상세 응답을 명시하며 수납 취소 action에는 요청 body가 없다.

## 조회와 실패 처리

대시보드는 취소되지 않은 청구서만 합산한다. 청구 총액·수납 총액·상태별 건수·전체
건수는 하나의 청구 집계 쿼리로 계산하고, 비목별 합계는 별도 항목 집계 쿼리로
계산한다. 빈 달은 모든 합계와 건수를 0으로 반환한다.

월 생성은 학생별 성공·건너뜀·오류 건수를 반환한다. 동시 생성 시 DB의 활성 청구
부분 유일성 제약이 마지막 방어선이며, 이미 다른 요청이 만든 활성 청구는 중복 생성
대신 건너뜀으로 처리한다. 일회성 비목의 월 교차 경합, 비용 수정 대 청구 생성,
후보 스냅샷 이후 비용 INSERT는 PostgreSQL 행 잠금 테스트로 검증한다.
학생 식별자 변경 거부, soft-delete와 생성의 경합, 동일 직접 배정의 201/400 직렬화도
같은 PostgreSQL 축에서 검증한다.

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
