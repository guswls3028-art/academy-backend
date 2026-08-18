# 신규 테넌트 온보딩 실행표

이 파일은 [custom-domain.md](custom-domain.md)의 절차를 빠짐없이 실행하기 위한
상태 기록 템플릿이다. 신규 작업마다 `<tenant-code>.md` 메모에 아래 표를 복사해
사용한다. 명령과 정책은 정본 문서를 따르고 이 실행표에 다시 복제하지 않는다.

## 입력

| 항목 | 값 |
|---|---|
| 정식 표시명 | `<name>` |
| 테넌트 코드 | `<code>` |
| 운영 ID | `<id>` |
| apex / www | `<domain>` / `www.<domain>` |
| 대표 표시명 | `<owner-display-name 또는 G7까지 유예>` |
| 초기 이용 정책 | `<계약 기간 또는 승인된 무기한 과금 제외>` |
| 로고 유형 | `투명 / 단색 배경 / 사진·그라데이션 배경` |
| 브랜드 팔레트 | `surface / surfaceSoft / foreground / accent` |

대표 로그인 ID, 임시·최종 비밀번호, 플랫폼 운영 계정은 기록하지 않는다.

## 실행 게이트

작업 재개 시 첫 번째 미완료 항목부터 시작한다. 실제 변경을 다시 실행하기 전에
해당 단계의 읽기 전용 확인 또는 dry-run을 반복한다.

- [ ] **G0 입력 확정**
  - 코드·ID·도메인·표시명·이용 정책·로고 원본 확정
  - 대표 정보는 G7 전까지 유예할 수 있으며 그동안 owner를 생성하지 않음
  - 증거:
- [ ] **G1 충돌 확인**
  - 운영 DB ID·코드와 기존 도메인 소유 관계 확인
  - 증거:
- [ ] **G2 Cloudflare 준비**
  - zone 생성, NS 1·2차 발급·전달
  - 증거:
- [ ] **G3 코드·브랜딩 준비**
  - backend host/CORS/CSRF
  - frontend registry, 로그인, 공용 헤더, 학생앱, 성적표, OG/PWA, 정적 에셋
  - typecheck·lint·build와 1366/390, 역할·라이트/다크 시각 검증
  - 증거:
- [ ] **G4 위임·정식 배포**
  - 1.1.1.1·8.8.8.8에서 발급 NS 확인
  - backend 상시 development·임시 preproduction·무중단 운영 배포 통과
  - frontend quality gate 통과
  - 증거:
- [ ] **G5 운영 DB·구독**
  - `provision_tenant` dry-run과 실제 적용
  - 기간 계약이면 계약값으로 `extend_subscription` dry-run과 실제 적용
  - 명시적으로 승인된 무기한 이용이면 운영 SSM의
    `BILLING_EXEMPT_TENANT_IDS`에 해당 ID만 추가하고 기존 ID를 보존
  - API ASG 무중단 교체 뒤 런타임 예외 목록, Program의 `active` 상태,
    만료일·다음 결제일 `NULL`, `is_subscription_active=True`,
    `audit_billing_fields --tenant <code>` 성공을 재조회
  - 증거:
- [ ] **G6 Pages·HTTPS**
  - apex/`www` Pages 등록과 CNAME 활성화
  - 두 `/login` URL HTTP 200, 인증서·제목·favicon·OG 확인
  - 증거:
- [ ] **G7 대표 계정 1회 생성**
  - 개발자 콘솔 테넌트 상세의 소유자 수 선확인
  - 기존 소유자 0명일 때만 생성
  - 생성 후 소유자 수 증가와 role `owner` 확인
  - 첫 접속 안내 상태는 계정 생성 시 자동 준비되므로 별도 플래그를 설정하지 않음
  - 증거:
- [ ] **G8 실제 로그인·인계**
  - 커스텀 도메인에서 임시 비밀번호 인증
  - 대표자가 최초 비밀번호 변경
  - 새 비밀번호 재로그인, 브랜드·role·tenant isolation·빈 초기 데이터 확인
  - 권유형 계정 안내에서 표시 ID와 내 정보 이동을 확인하고, 재로그인 시 다시 뜨지 않는지 확인
  - 증거:

## 완료 판정

G0~G8이 모두 체크돼야 운영 완료다. 최초 비밀번호 변경 화면까지만 도달한 경우
`초기 로그인 인증 완료 · 최초 비밀번호 변경 대기`로 기록하고 G8은 열어 둔다.
외부 인계나 고객 입력을 기다리는 동안에도 이미 통과한 게이트의 증거는 유지한다.
