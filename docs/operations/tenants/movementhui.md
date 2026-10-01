# 이동휘원소 과학연구소 — 온보딩 메모

**기준일:** 2026-10-01 KST

**상태:** 운영·대표 계정 최초 비밀번호 변경 확인 완료 · 개인 알림톡 채널 템플릿 검수 대기

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
- [ ] **G8 실제 인계** — 대표자 최초 비밀번호 변경 후 admin 화면·tenant isolation 확인

2026-10-01 운영 감사에서 활성 owner 1명, usable password,
`must_change_password=false`를 확인했다. 최초 비밀번호 변경 대기는 종료됐으며,
기존 G8의 별도 사용자 화면·tenant isolation 인계 증거는 이 감사로 대체하지 않는다.

## 개인 알림톡 채널과 설정 점검

- 사용자가 지정한 `동휘원소` 검색 ID와 PFID 끝자리 `Q7tR`을 공용 Solapi
  계정의 채널 상세 조회로 확인하고 tenant `10`에 새 channel binding을 등록했다.
- 공용 승인 템플릿 10종을 전달 동작이 동일하게 복제하고 10종 모두 검수 요청했다.
  현재 `INSPECTING:10`, 승인 `0/10`, binding은 `pending_templates`다.
- 검수 중에는 기존 공용 경로를 유지한다. 10개 template mapping의 본문·버튼·강조
  구조 지문 일치와 10개 공용 경로 해석을 운영 DB 재조회로 확인했다.
- 전용 채널은 아직 활성화되지 않았다. 운영자가 승인 상태를 재조회한 뒤
  `configure_tenant_alimtalk_channel --tenant-code movementhui --channel-id <등록된 PFID>
  --apply --activate-if-ready`로 동기화·활성화하고, 모든 mapping이 승인됐고 실제
  route가 `tenant_verified`인지 확인해야 한다. 검수 반려는 동일 binding에서
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
- 고객 실발송 없이 공급자 등록·검수 상태, DB 영속성, route를 검증했다.
  검수 승인·전용 채널 활성화·통제된 수신 검증은 후속 완료 조건이다.

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
