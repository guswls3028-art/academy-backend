# 이동휘원소 과학연구소 — 온보딩 메모

**기준일:** 2026-10-15 KST

**상태:** 운영·대표 계정 정상 · 전용 알림톡 10종 승인·라우팅 및 통제번호 1건 전송 성공 확인 · 신규 학생/학부모 비밀번호 기본 방식 설정 완료

**운영 도메인:** `movementhui.com`

**테넌트:** ID `10`, code `movementhui`

## 고객 메모

- 담당: 이동휘 강사님
- 브랜드: 이동휘원소 과학연구소
- 디자인: 로고의 딥 네이비·노란색 계열
- 운영 방식: 기존 엑셀 성적표·출석부를 병행하며 점진 전환
- 중요 흐름: 성적표 미리보기/발송, 학생·학부모 상시 열람, 학생 프로필 사진,
  오답노트 자동 생성, 재시험 대상 관리, 외부 플랫폼이 아닌 자체 영상 재생
- 학생 관리 희망 항목: MBTI, 취미, 목표 대학, 전년도 평균 등급,
  메가스터디 ID
- 성적표: 4주 코칭 단위 초안을 기준으로 실제 사용 후 양식 조율

계정 ID와 초기 비밀번호는 이 문서에 저장하지 않는다.

## 브랜드 기준

- 원본 로고 배경 실측: `#1A253B`
- 주 강조색: `#FFDB5A`
- 로그인·학생앱·성적표는 네이비를 주색, 노란색을 상태/포커스 강조로 사용
- 로고 원본은 비율·문구를 바꾸지 않고 정적 리소스 크기만 파생
- 로그인 화면은 흰 공용 카드가 아니라 딥 네이비 실험실 장면과 원자 궤도,
  옐로 로그인 액션으로 구성
- 내부 공용 헤더는 로고 배경 `#1A253B`에서 `#263653`을 거쳐 현재 헤더로
  사라지는 브랜드 리본을 사용하고, 관리자·선생·학생·학부모와 라이트·다크에서
  같은 계약을 적용

## 진행 상태

- [x] **G0 입력 확정** — 코드·ID·도메인·브랜드·30일 온보딩 기간
- [x] **G1 충돌 확인** — 운영 ID `10`, code `movementhui`, 도메인 소유 관계
- [x] **G2 Cloudflare 준비** — zone·NS 발급, 가비아 1·2차 등록
- [x] **G3 코드·브랜딩 준비** — backend host/origin, frontend 전체 경계,
  데스크톱·390px와 역할·라이트/다크 로컬 검증
- [x] **G4 위임·정식 배포** — Cloudflare·Google 공용 DNS 확인,
  backend·frontend 정식 배포
- [x] **G5 운영 DB·구독** — 최초 provision 완료. 현재 contract 구독 활성,
  만료일·다음 청구일 `2026-11-10` 운영 감사 확인
- [x] **G6 Pages·HTTPS** — apex/`www`·CNAME 활성화와 두 호스트 HTTP 200
- [x] **G7 대표 계정** — 개발자 콘솔에서 1회 생성, owner 초기 로그인 인증 확인
- [x] **G8 실제 인계** — 2026-10-01 운영 감사에서 owner 활성·usable password·최초 변경 완료 확인

2026-10-01 운영 감사에서 활성 owner 1명, usable password,
`must_change_password=false`를 확인했다. 최초 비밀번호 변경 대기는 종료됐으며,
기존 G8의 별도 사용자 화면·tenant isolation 인계 증거는 이 감사로 대체하지 않는다.

## 개인 알림톡 채널과 설정 점검

- 사용자가 지정한 `동휘원소` 검색 ID와 PFID 끝자리 `Q7tR`을 공용 Solapi
  계정의 채널 상세 조회로 확인하고 tenant `10`에 새 channel binding을 등록했다.
- 공용 승인 템플릿 10종을 전달 동작이 동일하게 복제하고 10종 모두 검수 요청했다.
  2026-10-04 공급자 재조회에서 `APPROVED:10`과 10개 mapping의 본문·버튼·강조
  구조 지문 일치를 확인했다.
- 같은 날 15:45 KST 공식 명령으로 기존 binding을 `active`로 전환했다.
  16:01 KST 독립 재조회에서 DB 영속성, 공급자 승인 `10/10`, 지문 일치 `10/10`,
  실제 route `tenant_verified:10`을 확인했다. 새 템플릿 생성·검수 요청은 없었다.
- 재점검·복구는 등록된 binding과 공급자 상태를 대조한 뒤
  `configure_tenant_alimtalk_channel --tenant-code movementhui --channel-id <등록된 PFID>
  --apply --activate-if-ready`로 동기화하고, 모든 mapping이 승인됐고 실제
  route가 `tenant_verified`인지 확인한다. 검수 반려는 동일 binding에서
  공급자 사유를 확인하고 재검수한다. 절차·실패 폐쇄·공용 공급자 경계는
  [알림톡 도메인 문서](../../domain/messaging-alimtalk.md#6-provider채널-정책)가 소유한다.
- 사용자가 제공한 연락처를 대표·본부 전화번호에 동일하게 저장하고 재조회했다.
  주소는 아직 미입력이다. 채널 관리자 번호에서 다른 연락처나 주소를 추정하지 않는다.
- `audit_tenant_onboarding movementhui --tenant-id 10 --domain movementhui.com
  --billing-mode contract --messaging-mode approved --require-owner
  --require-owner-handoff`의 16개 항목과 최종 `TENANT_ONBOARDING_AUDIT_PASS`를 확인했다.
  도메인 소유, host/CORS/CSRF, Program 브랜딩·기능, 구독, owner, 안전 기본값을 포함한다.
- apex·`www` HTTPS는 모두 HTTP 200이다. 메시징 활성, 운영 hold 없음, 기존 클리닉
  예약 생성·변경·취소·하원 config의 알림톡 모드와 연결 템플릿을 확인했다.
  다른 자동발송 선택·발송 단가·잔액은 기존 값을 유지했다.
- 고객 실발송 없이 공급자 승인, DB 영속성, 전용 route를 검증했다.
  지정 수신자에 대한 실제 수신 시험은 하지 않았으므로 전달 성공 증거로 해석하지 않는다.

## 현재 발급된 네임서버

```text
1차: barbara.ns.cloudflare.com
2차: thaddeus.ns.cloudflare.com
```

Cloudflare Pages와 apex/`www` CNAME은 활성화됐고 두 호스트 모두 HTTP 200을
확인했다.

## 별도 제품 확인

학생 관리 희망 항목은 현재 고정 컬럼으로 제공되지 않는다. 기존 `memo`에 임시로
합치지 말고, 테넌트별 학생 프로필 필드 계약 또는 정식 공통 컬럼으로 구현 범위를
확정한 뒤 반영한다. 고객 데이터 입력 전에 UI·엑셀 import/export·권한·학생앱
노출 여부를 함께 검증한다.


## 2026-10-01 계정·알림톡 점검

운영 readback에서 ID 10 / `movementhui`, apex primary 및 www domain,
allowed hosts/CORS/CSRF, Program, billing, owner handoff와 approved messaging
온보딩 검사가 모두 통과했다. 초기 비밀번호 제품 규칙은
[학부모 계정](../../domain/parent-account.md)의 현재 계약을 따른다.
당시 안내한 본인 번호 뒤 4자리 방식은 2026-10-02 명시 선택 계약의 저장된
학원 설정을 의미하지 않는다. 2026-10-15 재조회에서 두 역할이 미설정인 것을
확인하여 아래와 같이 보완했다. 설정 변경은 새 계정에만 적용한다.
계정 안내 URL은 primary domain인 `https://movementhui.com`이다.

사용자가 제공한 채널은 `https://pf.kakao.com/_xnjixin`,
`동휘 원소 과학 연구소 질문방` / 검색 ID `동휘원소`이다.
공식 공급사 조회에서 등록된 기존 binding과 일치했다.
당시 `configure_tenant_alimtalk_channel` dry-run 결과 required/matched 10,
APPROVED 0, INSPECTING 10, binding `pending_templates`이다.
이 검수 대기는 2026-10-04 승인 재조회 및 공식 명령 활성화로 종료됐다.
활성화 SSM 실행 ID는 `f6071641-ae32-4a3a-9b7e-5e1b4273819c`,
독립 readback은 `0f365dcc-e8bf-4771-aefd-4776270ce162`다.
실제 학생·학부모에게 QA 발송하지 않았다.

## 2026-10-15 사용 준비 재점검과 수정

- 운영 온보딩 감사 16개 항목 통과. tenant `10` / `movementhui`, primary domain
  `https://movementhui.com`, 대표 계정과 구독 활성, 메시징 사용 중이며 운영 hold 없음.
- 공급사 실조회에서 전용 채널 검색 ID `동휘원소`와 기존 PFID 끝자리 `Q7tR` 일치.
  필수 템플릿 `10/10`은 공급사·DB 모두 `APPROVED`, 전달 구조 지문도 공급사·DB와
  양쪽 채널에서 `10/10` 일치하며 실제 route는 모두 `tenant_verified`다.
- **수정:** `account_password_policy.student_mode`와 `parent_mode`가 모두 `null`이었다.
  사용자가 전달한 요청에 따라 정확한 tenant 10의 미설정 두 키만 `phone_last4`로
  저장했다. 운영 감사 로그를 남겼고 독립 재조회로 영속성을 확인했다.
  기존 학생·학부모 계정 2개의 비밀번호 hash와 다른 설정은 보존했다.
  정책 변경은 이후 신규 계정에만 적용하며 실제 등록 최종창에서는 직원이
  각 역할의 방식을 명시 선택한다. 학생은 학생 본인 번호, 학부모는 학부모 본인
  번호를 사용한다. 번호가 없거나 두 역할의 번호가 같으면 다른 방식을 선택해야 한다.
  기존 공유 학부모 계정은 새 자녀를 연결해도 초기화하지 않는다.
- 승인받은 통제번호에 `send_tenant_alimtalk_channel_test`로 인증번호 양식 1건만
  발송했다. 고정 요청 키 `movementhui-ready-20261015-01a13eb7`을 사용했으며,
  17:53 KST 공급사 독립 조회 결과 `COMPLETE`, total/sentSuccess `1/1`,
  sentFailed/sentPending/sentReplacement `0/0/0`, `ATA`, `statusCode=4000`이다.
  이후 사용자가 해당 인증번호 알림톡의 단말 수신을 확인했다.
  학생·학부모에게 테스트 발송하지 않았다.
- 설정 저장 후 사전검사에서 예약·지연·재시도·미확정·24시간 실패는 모두 0,
  활성 자동발송 4개 설정의 승인/연결 문제는 0이었다. 학원 발송 단가 0과
  잔액 0은 기존 값으로 유지했고 현재 발송을 차단하지 않는다. 공용 공급사 잔액은
  양수임을 확인했으며 충전이나 단가 변경은 하지 않았다.
- 현재 사이트 버전 `687b84aafb84c5b025659b76e1b3c094d1b0bacc`와
  [공식 배포 검증](https://github.com/guswls3028-art/academy-frontend/actions/runs/38678689080/attempts/2)
  의 동일 artifact 증거를 대조했다. 계정 등록/로그인, 학생·학부모 학습/평가,
  과제·자료·클리닉·공지·Q&A와 데스크톱/모바일 영상 갱신 등을 포함한
  격리 개발 실사용 30건이 통과했고 실패·skip·flaky는 0, 두 QA tenant/user
  cleanup도 0이다. 운영 API digest도 이 검증의
  `sha256:4e63eefb4a28f75682d3e43b3b45d308a890c744f6f30999ff3bc19122c057ee`와 일치했다.
  이동휘 공개 로그인/가입 화면은 데스크톱 1366px와 모바일 390px에서 진입·취소
  복귀를 확인했고 가로 넘침과 브라우저 오류는 없었다.
  이 증거는 공통 제품 경로 검증이며 이동휘 고객 데이터로
  모든 업무를 새로 수행했다는 뜻은 아니다. 이번 수정은 기존 설정 적용으로
  애플리케이션 코드 변경이나 재배포가 필요하지 않았다.

계정 안내는 학생 마스터 저장만으로 발송하지 않고 첫 ACTIVE 수강 확정 때 발송한다.
접속 링크는 현재 primary domain을 사용한다. 등록 선택과 기존 계정 보존 계약은
[초기 비밀번호 정책](../../ssot/account-initial-password-policy.md), 발송 시점과
재안내는 [학부모 계정](../../domain/parent-account.md)을 따른다.

## 2026-10-15 등록·수강·계정 안내 연속 검증

운영과 동일한 API digest `4e63eefb4a28f75682d3e43b3b45d308a890c744f6f30999ff3bc19122c057ee`의
격리 개발 환경에서 이동휘의 저장된 초기 비밀번호 정책과 primary domain을 적용한
일회용 QA tenant로 실제 HTTP API, DB 재조회, 큐·메시징 워커를 연결해 검증했다.
운영 고객 계정을 만들거나 기존 학생·학부모에게 발송하지 않았다.

- 학생 등록 후 재조회로 저장과 로그인 ID를 확인했다. 수강 전 안내 발송은 0건이며,
  최초 안내용 비밀번호는 암호화 보관됐다.
- 첫 ACTIVE 수강 연결 후 재조회로 수강 관계를 확인했다. 학생·학부모 안내가
  각각 1건 생성됐고, 안내 보장 후 임시 비밀번호 암호문은 비워졌다.
- 학생은 학생 본인 번호 뒤 4자리, 학부모는 학부모 본인 번호 뒤 4자리로 실제
  로그인하고 사용자 정보를 조회했다. 학부모가 명시 선택한 자녀도 등록 학생과 일치했다.
- 같은 수강 연결을 반복해도 추가 안내는 0건이었다. 개발 메시징 워커 처리 성공은
  2건, 실패는 0건이며 외부 전송을 차단한 개발 mock 공급자 영수증을 확인했다.
- 워커의 실제 공급자 요청에서 두 역할의 ID·비밀번호가 로그인에 성공한 정보와
  일치하고, `사이트링크=https://movementhui.com`, SMS 대체 금지인 것을 확인했다.
- 운영 tenant 10의 학생·학부모 등록 안내 템플릿을 공급사에서 별도로 읽어 대조했다.
  두 양식 모두 공급사·DB `APPROVED`, 본문·버튼 구조 지문 일치,
  전용 채널 끝자리 `Q7tR`의 `tenant_verified` 경로였다. 치환 후 미해결 변수는 없고
  접속 링크는 두 본문 모두 `https://movementhui.com`이다. 별도 버튼은 없는 양식이다.
- QA tenant `822`와 사용자 6명(학생 2명 포함)을 공식 정리 명령으로 삭제한 후
  tenant/user, 활동 감사, 미사용 인증 토큰 및 영상 관련 잔여가 모두 0임을 확인했다.

실행 증거: 흐름 `2f5f8360-8e6d-435d-a6bd-64aa88448f2a`, 공급자 요청 대조
`e978acad-7e6f-4724-a7ae-30a8355cb537`, 정리
`00ae7849-1b02-4c75-9b4e-e2599c97bab8`, 운영 양식 대조
`957c5715-e957-46fe-89b0-add342e42666` (모두 SSM Success).
이 결과는 격리 환경의 신규 계정 흐름과 운영 승인·라우팅을 각각 확인한 증거이며,
운영 신규 학생의 계정 안내를 실제 단말까지 전송했다는 뜻은 아니다.
