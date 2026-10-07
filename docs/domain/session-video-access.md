# 차시별 영상 권한

일반 강의 영상은 해당 tenant의 활성 학생·ACTIVE 강의 수강등록과 **정확한
SessionEnrollment**가 모두 있어야 시청할 수 있다. N차시 등록은 앞뒤 차시에
권한을 주지 않는다. 출결·진도·영상별 재생 설정은 차시 등록을 대신하지 않는다.

학생 및 학부모의 선택 자녀는 영상 홈에서 등록된 차시만 보고, 영상 수·시간·진도
집계도 그 차시를 기준으로 받는다. 직접 URL을 입력하더라도 차시 목록, 재생 시작,
access_check, 진도·댓글·좋아요 등 공통 context 소비자는 같은 권한을 확인한다.
SessionEnrollment의 tenant, 수강·학생·강의 tenant와 강의 일치가 모두 필요하다.

차시 등록을 해제하면 해당 차시의 새 재생과 기존 토큰 refresh/renew/heartbeat는
403으로 거절된다. 다른 등록 차시는 계속 정상 이용할 수 있다. 강의 수강등록을
INACTIVE로 바꾸면 일반 수강 권한은 전체 회수된다. 출결·성적·진도 이력은 이
권한 검사 변경으로 삭제하거나 다시 작성하지 않는다.

명시적 PUBLIC 영상과 시스템 공개 영상 공간은 기존 공개 계약을 유지한다.
교직원이 별도 승인한 inactive/direct 영상 권한도 기존 정확한 영상 단위 계약을
유지한다([direct-video-access.md](direct-video-access.md)). 일반 ACTIVE 수강의
차시 누락을 그 예외로 자동 보완하지 않는다.

판정 소유자는 `academy/application/use_cases/student_video_access_context.py`,
차시 관계 조회는 `repositories_video.video_session_memberships` 및
`session_enrollment_exists`, 토큰/발급 시 재검사는 `get_effective_access_mode`다.
학생 영상 홈·통계는 동일 관계 조회로 범위를 제한한다.

DB migration이나 API 응답 형식 변경은 없다. 구 frontend도 서버에서 같은 차시
검사를 받으며 페이지 강제 새로고침이나 일괄 세션 초기화를 요구하지 않는다.
이미 발급된 CDN URL 자체는 그 서명 만료까지 유효할 수 있으며, API의 즉시
권한 회수와 CDN 캐시/이미 내려받은 데이터의 삭제를 동일한 보장으로 취급하지 않는다.

감독형 재생을 새로 발급할 때 API 응답·토큰과 저장된 재생 세션은 하나의 절대 만료
시각을 사용한다. 발급 중 초 경계가 지나도 세션만 1초 늦게 남지 않는다. 만료된
재생은 현재 수강·차시 권한을 다시 확인한 뒤 새로 발급한다. 기존 세션 데이터의
마이그레이션이나 응답 형식 변경은 없다.

검증: `apps/domains/video/tests/test_session_video_entitlement.py`는 8차시 중
3차시만 등록, 홈/통계 범위, 정상 재생 후 차시 해제와 기존 토큰 재검사, 다른
차시 유지, 강의 INACTIVE, 공개 영상, 다른 tenant 관계를 실제 DB/API로 검증한다.
`tests/test_video_access_security.py`, `tests/test_direct_video_entitlement.py`와
inactive entitlement 회귀를 함께 실행한다. 운영 모양의 로그인·브라우저/CDN 검증은
격리 개발 tenant에서 수행하고 tenant/user residue zero를 확인한다.


## 학생 재생 정책·진도·댓글 일관성

- 활성 수강의 목록/재생 권한은 명시 차단을 먼저 적용하고, 교사 지정
  `is_override`를 출결 기본값보다 우선한다. 지정된 감독 시청도 완료 기준을
  만족하면 복습으로 전환한다. 목록은 미리 조회한 권한·진도·출결을 사용하며
  영상별 추가 조회를 만들지 않는다. 세션 소속·테넌트 검증, 비활성 수강의
  정확한 entitlement와 직접 접근의 별도 읽기 전용 경계는 유지한다.
- `block_speed_control`은 감독/복습 모드 기본값과 배속 override보다
  우선한다. 서버 정책은 최대 1배속과 배속 UI 비활성을 함께 반환한다.
  건너뛰기 차단·예산과 워터마크 제한을 완화하지 않는다.
- 학생 및 선택 자녀의 진도 저장은 `services/student_progress.py`에서
  영상/수강별 행 잠금과 유일 키를 이용해 병합한다. 지연·중복·재시청 요청은
  저장된 진도 최댓값과 완료 플래그를 내릴 수 없으며 `last_position`은
  뒤로 이동할 수 있다. 완료 임계값과 raw `completed=False` 표현은 유지한다.
  건너뛰기 사용량은 변경하지 않는다. 비활성 entitlement는 기존 잠금과
  재검증을 통과한 뒤 같은 병합을 사용하며 직접 접근은 DB 진도를 쓰지 않는다.
- 교사의 명시 진도 수정/초기화는 기존 staff 전용 경로를 사용한다.
  학생 병합을 모델 전체에 적용하거나 완료 마커를 새로 생성하지 않는다.
  다음 학생 저장은 초기화 후의 DB 상태를 기준으로 한다. 기존
  `proctored_completed_at`의 교사 관리 규칙은 변경하지 않는다.
- 댓글 GET은 삭제된 원문을 빈 내용의 placeholder로 반환하면서 그 아래의
  기존 살아 있는 답글을 보존한다. 학생 탈퇴 제외, 동일 테넌트/영상 범위,
  최상위 100개·답글 20개 제한은 유지한다. 삭제된 답글은 노출하지 않는다.
  삭제된 원문에 대한 신규 답글 POST는 400으로 거절하고 행/카운터를 쓰지 않는다.
  원문 행 잠금으로 삭제와 답글 생성을 직렬화하고 댓글 쓰기와 카운터 변경은
  같은 트랜잭션에서 처리한다. 작성된 내용의 물리 삭제나 하위 답글 연쇄 삭제는 없다.
- [학생 시청 UI 계약](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/STUDENT-VIDEO-WATCH.md)은 삭제 placeholder 아래 답글 표시와 POST 실패 후 재조회/재시도를
  담당한다. backend 응답 필드와 기존 soft-delete 저장 구조는 유지한다.
- 회귀 검증: `tests/test_student_video_contract_regressions.py`와 기존
  student progress/session/direct/inactive entitlement 및 video access/security
  테스트. 실제 행 잠금의 동시성 검증은 PostgreSQL 환경에서 수행한다.
