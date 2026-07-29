# 이동휘원소 과학연구소 — 온보딩 메모

**기준일:** 2026-07-29 KST

**상태:** 가비아 → Cloudflare 네임서버 위임 확인 · 운영 배포 진행 중

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

## 진행 상태

- [x] Cloudflare zone 생성
- [x] Cloudflare 네임서버 발급
- [x] backend 허용 host/CORS/CSRF 코드 준비
- [x] frontend tenant registry·로그인·학생앱·성적표·OG/PWA 코드 준비
- [x] 로고 정적 리소스 준비
- [x] 범용 `provision_tenant` 명령과 회귀 테스트 준비
- [x] 가비아에 Cloudflare NS 1·2차 등록
- [x] Cloudflare·Google 공용 DNS 위임 확인
- [ ] backend 정식 배포
- [ ] frontend 정식 배포
- [ ] 운영 DB dry-run 및 실제 provision
- [ ] 개발자 콘솔에서 대표 계정 생성
- [ ] Pages apex/`www` 및 CNAME 활성화
- [ ] 데스크톱·모바일·owner 로그인·tenant isolation 확인

## 현재 발급된 네임서버

```text
1차: barbara.ns.cloudflare.com
2차: thaddeus.ns.cloudflare.com
```

Pages/CNAME 활성화와 외부 접속 완료 판정은 배포·운영 DB 프로비저닝 뒤 수행한다.

## 별도 제품 확인

학생 관리 희망 항목은 현재 고정 컬럼으로 제공되지 않는다. 기존 `memo`에 임시로
합치지 말고, 테넌트별 학생 프로필 필드 계약 또는 정식 공통 컬럼으로 구현 범위를
확정한 뒤 반영한다. 고객 데이터 입력 전에 UI·엑셀 import/export·권한·학생앱
노출 여부를 함께 검증한다.
