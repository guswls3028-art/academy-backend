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
| 대표 계정 입력 | `수신 대기 / 보안 입력 준비` (개인정보·비밀번호 미기록) |
| 과금 모드 | `contract / exempt` |
| 계약 이용기간 | `<days; exempt면 해당 없음>` |
| 메시징 모드 | 공용 승인 채널·템플릿 `approved` 기본, 고객의 전체 사용 중지 요청 시 `disabled` |
| 로고 유형 | `투명 / 단색 배경 / 사진·그라데이션 배경` |
| 브랜드 팔레트 | `surface / surfaceSoft / foreground / accent` |
| 안전 기본값 | `학생가입 수동승인 / 클리닉 수동승인 / 영상 동시접속 무제한` |
| 별도 승인 기능 | `<없음 또는 기능·승인 근거>` |

대표 로그인 ID, 임시·최종 비밀번호, 플랫폼 운영 계정, 공급사 credential은
기록하지 않는다. 과금·메시징·자동승인·영상 제한을 추정하지 않는다.

## 실행 게이트

G1 뒤에는 위임·Pages 연결, 코드·브랜딩, 운영 DB·구독·계정 준비를 병행한다. 재개 시 완료된 증거를 재사용하고 선행 조건이 충족된 항목부터 실행한다. 실제 변경을 재시도하기 전에 대상 상태를 다시 읽거나 dry-run으로 확인한다.

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
  - 1.1.1.1·8.8.8.8에서 발급 NS를 확인한 즉시 G6 연결 시작
  - 증거:
- [ ] **G3 소스·브랜딩·서비스 경계 준비**
  - backend host/API CORS/CSRF와 영상 R2 CORS origin
  - frontend registry, 로그인, 공용 헤더, 학생앱, 성적표, OG/PWA, 정적 에셋
  - 공유형 Video/AI/Tools/Analytics/큐는 별도 tenant 목록이 없음을 정본과 대조
  - 공용 채널과 승인 템플릿 사용 여부를 감사하고, 자동승인·영상 제한은 안전 기본값 유지
  - typecheck·lint·build와 1366/390, 역할·라이트/다크 시각 검증
  - 증거:
- [ ] **G4 운영 호환성·정식 배포**
  - 대상 도메인을 지원하는 현재 운영 revision 확인, 필요한 변경은 정식 배포
  - backend 상시 development·임시 preproduction·무중단 운영 배포 통과
  - frontend quality gate 통과
  - 신규 apex/`www` Origin의 R2 PUT 200, `Access-Control-Allow-Origin`,
    `ETag` 노출과 임시 multipart 잔여 0 확인
  - 증거:
- [ ] **G5 운영 DB·구독·안전 기본값**
  - 대상 ID·코드·도메인 dry-run. 운영 백엔드의 호환성과 충돌 없음을 확인한 뒤 실제 적용
  - `contract`면 계약값으로 `extend_subscription` dry-run과 실제 적용
  - `exempt`면 운영 SSM의 `BILLING_EXEMPT_TENANT_IDS`에 해당 ID만 추가하고
    기존 ID를 보존한 뒤 ASG 무중단 교체
  - 메시징·학생가입 자동승인·클리닉 자동승인·영상 동시접속 정책 readback
  - 아래 감사 명령을 owner 없이 먼저 통과
  - 증거:
- [ ] **G6 Pages·HTTPS**
  - G2 위임 확인 후 정확한 zone·Pages 프로젝트·target·기존 레코드를 대조하고 apex/`www`만 연결. G3~G5 진행 중에도 실행
  - Pages·DNS·인증서 상태를 먼저 기록. 실제 프런트가 준비되면 두 `/login` URL의 HTTP 200, 제목·favicon·OG 확인
  - 증거:
- [ ] **G7 원장과 요청된 학생·보호자 직접 등록**
  - G5 감사 PASS 뒤 운영 IAM 관리 경계에서 대상 ID·코드·apex/`www`와 과금·메시징 모드를 대조
  - [직접 등록 절차](custom-domain.md#대표-계정-표준-절차)에 따라 담당자 입력을 stdin으로 전달. 작업 전용 SecureString 사용 시 이름·유형·버전을 확인
  - 동일 계정·정보 재실행은 변경 없음. 기존 보호자 비밀번호 보존. 충돌은 전체 롤백 후 대상 대조
  - 결과 상태와 생성 감사 출처, `--require-owner` PASS만 기록. 인증된 콘솔 소유자 탭은 대표 계정 등록 대안
  - 증거:
- [ ] **G8 실제 로그인·인계**
  - 실제 운영 도메인에서 승인된 계정으로 임시 비밀번호 인증. 프리뷰·데모·별도 랜딩을 만들지 않음
  - 대표자가 최초 비밀번호 변경. 현재 로그인 세션에서 변경 권유를 미루고 업무를 시작한 경우 인계 대기로 기록
  - 새 비밀번호 재로그인, 브랜드·role·tenant isolation·초기 데이터 확인(승인된 체험 학생이 있으면 해당 등록 건과 대조)
  - 권유형 계정 안내에서 표시 ID와 내 정보 이동을 확인하고, 재로그인 시 다시 뜨지 않는지 확인
  - 실제 역할별 기본 화면·접근 범위·재로그인 결과를 확인하면 운영 주소와 이용 가능 범위를 먼저 안내. 추가 기능 QA 상태는 별도로 기록
  - 증거:
- [ ] **G9 최종 봉인**
  - `audit_tenant_onboarding`을 `--require-owner --require-owner-handoff`로
    다시 실행해
    `TENANT_ONBOARDING_AUDIT_PASS` 확인
  - apex/`www` 가용성, R2 browser PUT, 배포 revision, 역할별 1366/390 증거 연결
  - 요청한 기능·워커의 추가 실사용과 실패 복구는 격리 환경에서 확인. 비밀정보와 합성 QA 데이터가 남지 않았음을 확인
  - 증거:

## 감사 명령

G5에서는 owner 관련 플래그를 빼고 실행한다. G7 직후에는 `--require-owner`로
활성 owner 멤버십 한 건, 활성 사용자와 usable password를 확인한다. G9에서는
`--require-owner --require-owner-handoff`로 `must_change_password=false`까지 확인하고 G8의 대표자 재로그인 증거와 함께 기록한다.
`<billing-mode>`은 `contract` 또는 `exempt`다. `<messaging-mode>`은 공용 승인 채널의 기본값 `approved`, 고객이 전체 사용을 중지한 경우 `disabled`다.

```powershell
cd C:\academy\backend
.\scripts\v1\run-api-management-remote.ps1 -Command 'audit_tenant_onboarding <code> --tenant-id <id> --domain <domain> --billing-mode <billing-mode> --messaging-mode <messaging-mode> --require-owner --require-owner-handoff'
```

명령은 읽기 전용이며 Tenant/Domain/Program, runtime host/CORS/CSRF, 브랜딩,
기능 플래그, 구독 또는 예외, owner 중복·활성·usable password·최초 비밀번호
변경, 메시징 모드, 안전 기본값 중 하나라도
다르면 실패한다. DNS/Pages/인증서와 실제 R2 bucket CORS는 외부 경계이므로 이
명령의 성공으로 대체하지 않고 G4·G6·G9의 실측 증거를 별도로 남긴다.

## 완료 판정

G0~G9가 모두 체크돼야 운영 완료다. 비밀번호 변경 안내까지만 확인했거나 대표자가 변경을 미룬 경우
`초기 로그인 인증 완료 · 최초 비밀번호 변경 대기`로 기록하고 G8·G9는 열어 둔다.
외부 인계나 고객 입력을 기다리는 동안에도 이미 통과한 게이트의 증거는 유지한다.
