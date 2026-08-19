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
| 과금 모드 | `contract / exempt` |
| 계약 이용기간 | `<days; exempt면 해당 없음>` |
| 메시징 모드 | `disabled / 별도 승인된 approved` |
| 로고 유형 | `투명 / 단색 배경 / 사진·그라데이션 배경` |
| 브랜드 팔레트 | `surface / surfaceSoft / foreground / accent` |
| 안전 기본값 | `학생가입 수동승인 / 클리닉 수동승인 / 영상 동시접속 무제한` |
| 별도 승인 기능 | `<없음 또는 기능·승인 근거>` |

대표 로그인 ID, 임시·최종 비밀번호, 플랫폼 운영 계정, 공급사 credential은
기록하지 않는다. 과금·메시징·자동승인·영상 제한을 추정하지 않는다.

## 실행 게이트

작업 재개 시 첫 번째 미완료 항목부터 시작한다. 실제 변경을 다시 실행하기 전에
해당 단계의 읽기 전용 확인 또는 dry-run을 반복한다.

- [ ] **G0 입력 확정**
  - 코드·ID·도메인·표시명·과금 모드·메시징 모드·로고 원본 확정
  - 안전 기본값과 별도 승인 기능을 구분해 기록
  - 대표 정보는 G7 전까지 유예할 수 있으며 그동안 owner를 생성하지 않음
  - 증거:
- [ ] **G1 충돌 확인**
  - 운영 DB ID·코드와 기존 도메인 소유 관계 확인
  - 프런트 레지스트리의 ID·코드·호스트 중복 확인
  - 증거:
- [ ] **G2 Cloudflare 준비**
  - zone 생성, NS 1·2차 발급·전달
  - 위임 확인 전 Pages/CNAME 실제 변경 중단
  - 증거:
- [ ] **G3 소스·브랜딩·서비스 경계 준비**
  - backend host/API CORS/CSRF와 영상 R2 CORS origin
  - frontend registry, 로그인, 공용 헤더, 학생앱, 성적표, OG/PWA, 정적 에셋
  - 공유형 Video/AI/Tools/Analytics/큐는 별도 tenant 목록이 없음을 정본과 대조
  - 메시징은 별도 승인 전 `disabled`, 자동승인·영상 제한은 안전 기본값 유지
  - typecheck·lint·build와 1366/390, 역할·라이트/다크 시각 검증
  - 증거:
- [ ] **G4 위임·정식 배포**
  - 1.1.1.1·8.8.8.8에서 발급 NS 확인
  - backend 상시 development·임시 preproduction·무중단 운영 배포 통과
  - frontend quality gate 통과
  - 신규 apex/`www` Origin의 R2 PUT 200, `Access-Control-Allow-Origin`,
    `ETag` 노출과 임시 multipart 잔여 0 확인
  - 증거:
- [ ] **G5 운영 DB·구독·안전 기본값**
  - `provision_tenant` dry-run과 실제 적용
  - `contract`면 계약값으로 `extend_subscription` dry-run과 실제 적용
  - `exempt`면 운영 SSM의 `BILLING_EXEMPT_TENANT_IDS`에 해당 ID만 추가하고
    기존 ID를 보존한 뒤 ASG 무중단 교체
  - 메시징·학생가입 자동승인·클리닉 자동승인·영상 동시접속 정책 readback
  - 아래 감사 명령을 owner 없이 먼저 통과
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
- [ ] **G9 최종 봉인**
  - `audit_tenant_onboarding`을 `--require-owner`로 다시 실행해
    `TENANT_ONBOARDING_AUDIT_PASS` 확인
  - apex/`www` 가용성, R2 browser PUT, 배포 revision, 역할별 1366/390 증거 연결
  - 비밀정보와 합성 QA 데이터가 남지 않았음을 확인
  - 증거:

## 감사 명령

G5에서는 `--require-owner`를 빼고 실행한다. G9에서는 반드시 붙인다.
`<billing-mode>`은 `contract` 또는 `exempt`, `<messaging-mode>`은 `disabled`
또는 별도 승인된 `approved`다.

```powershell
cd C:\academy\backend
.\scripts\v1\run-api-management-remote.ps1 -Command 'audit_tenant_onboarding <code> --tenant-id <id> --domain <domain> --billing-mode <billing-mode> --messaging-mode <messaging-mode> --require-owner'
```

명령은 읽기 전용이며 Tenant/Domain/Program, runtime host/CORS/CSRF, 브랜딩,
기능 플래그, 구독 또는 예외, owner 중복·활성, 메시징 모드, 안전 기본값 중 하나라도
다르면 실패한다. DNS/Pages/인증서와 실제 R2 bucket CORS는 외부 경계이므로 이
명령의 성공으로 대체하지 않고 G4·G6·G9의 실측 증거를 별도로 남긴다.

## 완료 판정

G0~G9가 모두 체크돼야 운영 완료다. 최초 비밀번호 변경 화면까지만 도달한 경우
`초기 로그인 인증 완료 · 최초 비밀번호 변경 대기`로 기록하고 G8·G9는 열어 둔다.
외부 인계나 고객 입력을 기다리는 동안에도 이미 통과한 게이트의 증거는 유지한다.
