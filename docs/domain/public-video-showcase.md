# 공개 YouTube 영상

현재 구현 정본: `apps/domains/landing_public/models/video.py`,
`api/views/video_views.py`, `services/youtube.py`.
화면 계약: [frontend PUBLIC-VIDEOS.md](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/PUBLIC-VIDEOS.md).

## 목적과 권한

홈페이지 방문자는 로그인 없이 공개 영상과 대표 영상을 읽고 YouTube 공식 플레이어에서
재생한다. 원본 파일 저장, 수강용 영상, 인코딩/Batch 작업은 발생하지 않는다.
godmin의 신민·박철 운영 계정은 기존 공개 자료게시판의 두 지정 게시자 설정을 공유한다.
`PublicResourceBoardAccess`의 두 슬롯 모두 설정되어야 하며, 요청 계정이 해당 슬롯에
포함되고 활성 User 및 해당 tenant의 활성 owner/admin/teacher membership이어야 한다.
이름, 전역 staff/superuser, 다른 tenant의 역할은 우회 권한이 아니다.
권한 함수는 `services/publishing.py`가 자료와 영상을 함께 소유한다.

## 저장·공개 흐름

PublicVideo는 tenant + YouTube ID별 한 행이다. `draft_content`(제목·소개·분류·형태)를
저장해도 `published_content`는 바뀌지 않는다. 공개 시 서버에서 원본 메타데이터를 다시
조회하고 검증된 초안을 공개 스냅샷으로 복사한다. 비공개는 status만 변경하고 초안과
이전 공개 스냅샷을 보존한다. 공개 API는 published 상태만 반환한다.
삭제는 별도 확인을 거쳐 `deleted` 상태로 전환하며 공개/관리 목록·상세에서 모두 제외한다.
대표 해제와 삭제 이벤트를 같은 트랜잭션에 남긴다. 원본 YouTube는 변경하지 않는다.
감사·늦은 요청 재전송 방지를 위해 삭제 행과 스냅샷은 DB에 보존하며 제품 복원 API는 없다.
같은 YouTube 링크는 새 request_id로 다시 등록할 수 있다. 삭제된 최초 등록 요청을
재전송하면 409이며 삭제 재시도는 204다. 삭제 전후 다른 영상의 대표 설정은 보존한다.

영상의 자유 입력 분류는 `draft_content.category_label`(선택, 40자)이며 공개 시 스냅샷에
복사한다. 초기값은 빈 문자열이다. 이전 `category` 코드 세 개는 열린 구버전 편집기와의
호환 목적으로 보존하며 새 UI는 이 코드에 임의의 의미나 분류명을 붙이지 않는다.
구버전 PATCH가 category_label을 보내지 않으면 기존 사용자 작성 분류를 유지한다.

PublicVideoShowcase는 tenant별 대표 영상 0~1개다. 대표 지정과 공개, 비공개 시 대표 해제는
하나의 트랜잭션이다. 비공개 대표는 홈페이지의 기존 강사 프로필로 복귀한다.
같은 행의 title(필수, 60자, 기본 `영상`)과 description(선택, 220자, 기본 빈 문자열)은
홈페이지 영상 영역과 영상 목록에서 공통으로 사용한다. 지정 게시자가 명시적으로 저장하면
즉시 공개되며, 생략한 설정 필드는 유지한다. 대표 선택과 동일 revision을 검증한다.
모든 쓰기는 User → TenantMembership → PublicResourceBoardAccess 순으로 잠근다.
tenant의 access 행으로 편집자 간 쓰기를 직렬화하고 video/showcase revision으로
오래된 화면의 덮어쓰기를 409로 거절한다. 권한 회수와 동일 잠금 순서를 유지한다.
공개/비공개/삭제 요청에는 두 revision이 필요하다. 감사를 위한 PublicVideoEvent에
tenant/actor/action/video revision 및 대표 변경 ID를 남긴다.

등록은 tenant + request_id로 재시도 가능하다. 같은 작성자·원본·최초 초안이면 저장된
결과를 반환하며 다른 요청 내용이면 409다. 동일 YouTube의 새 요청에는 해당 tenant의
existing_id만 반환한다. 테넌트 밖 ID는 404 또는 권한 거부로 처리한다.

## API

모든 경로는 `/api/v1/landing-public/videos/` 아래이며 Cache-Control은 no-store다.

| 경로 | 접근 / 동작 |
| --- | --- |
| GET 루트, `<uuid>/` | 공개 목록(12개 페이지), 상세 |
| GET `showcase/` | 공개 대표 영상 또는 null |
| GET `capabilities/` | 현재 계정 can_publish |
| POST `resolve/` | 지정 게시자 링크 메타데이터 조회 |
| GET/POST `manage/` | 지정 게시자 목록/임시저장 등록 |
| GET/PATCH `manage/<uuid>/` | 관리 상세/초안 수정(expected_revision) |
| DELETE `manage/<uuid>/` | query의 expected_revision/expected_showcase_revision 검증 후 삭제, 성공/재시도 204 |
| POST `manage/<uuid>/publish/`, `unpublish/` | 두 revision 검증 후 공개/비공개 |
| GET/PATCH `manage/showcase/` | 대표 ID/revision/제목/소개 조회 및 명시된 설정 수정 |

## 탐색과 YouTube 원본 통계

공개 목록은 `kind=landscape|vertical`, `sort=latest|popular|oldest`, `q`(최대 90자)를
검증한다. kind 생략은 구버전과 같이 모든 형태이며 기본 정렬은 latest다. DRF의 렌더러용
format 예약어와 구분한다. 공개 스냅샷의 형태/제목/자유 분류와 원본 채널에만 검색을 적용하고,
전체 결과를 정렬한 뒤 12개씩 나눈다. 초안·타 tenant·비공개·삭제 행은 포함하지 않는다.
latest/oldest는 원본 업로드일(없으면 홈페이지 공개일), popular는 실제 YouTube 조회수다.
동률은 날짜/ID로 안정적으로 정렬하며 미확인 조회수는 0이 아닌 null로 마지막에 둔다.
결과가 있는데 전부 미확인이면 503으로 재시도/최신순 복귀를 안내한다. 빈 검색은 200이다.

`academy/adapters/youtube_statistics.py`는 서버 전용 `YOUTUBE_DATA_API_KEY`로 공식
`https://www.googleapis.com/youtube/v3/videos`의 snippet/statistics만 조회한다.
키는 SSM의 환경별 API env에서 주입하고 브라우저·URL·로그에 넣지 않는다. YouTube Data API v3
전용 제한 키를 쓰며 Gemini 키의 권한을 넓히지 않는다. 개발 API는 배포 publisher가
`/academy/providers/development/youtube-api-key`의 별도 SecureString을 읽어 주입한다.
운영 키와 같으면 배포를 중단하며 worker/preprod에는 YouTube 키를 전달하지 않는다.
개발 deploy OIDC 역할만 이 정확한 원본 parameter 읽기 권한을 추가하며 런타임 SSM 경계는 유지한다.
API 활성화/약관과 두 키 준비 후 격리 개발 실조회 검증을 통과해야 UI를 승격한다.
서버는 검증된 ID 최대 50개, 연결/읽기 3/5초, 전체 본문 읽기 12초, 256KiB, redirect 금지를
적용한다. 타 ID/잘못된 응답은 실패 폐쇄하며 누락 ID는 미확인이다.

공개 목록·상세·대표의 GET/HEAD/OPTIONS는 DB와 공급자에 쓰기 없이 저장된 통계만 읽는다.
30일 초과 값은 SQL 정렬과 응답에서 제외하되 읽기 요청으로 지우지 않는다. 운영의
`SafeMethodDatabaseWriteMiddleware`를 우회하거나 끄지 않는다. 이전 요청 중 갱신 방식은
이 보호 규칙과 충돌해 500을 일으켰으므로 공개 POST와 별도 정기 작업으로 대체했다.
`services/video_statistics.py`는 영상 공개 POST가 커밋된 뒤 해당 영상만 갱신한다.
별도 `Public Video Statistics Maintenance` workflow는 15분 간격으로
`refresh_public_video_statistics --execute`를 실행한다. 활성 tenant별 공개 대상 최대 50개를
잠가 선점하고 네트워크 전에 잠금을 푼다. 정상 확인은 6시간 뒤, 장애는 최소 5분 뒤부터
다음 정기 실행에서 재시도한다. 원본 확인 실패에도 완료된 공개와 최근 정상 값은 유지한다.
조회수/원본 날짜/확인·재시도 시각 네 파생 필드만 갱신하며 게시자 revision·문구·공개일을
바꾸지 않는다. 응답 적용 시 공개 상태/원본 ID를 다시 확인해 비공개/삭제 경합을 보존한다.
일시 장애는 최근 정상 값을 유지하고 30일을 넘은 값은 노출·정렬에서 제거한다.
같은 workflow가 `expire_public_video_statistics --execute`로 모든 tenant/상태의
28일 초과 파생 필드를 비운다. 기본 명령은 계획만 출력하며 사용자 작성 데이터/영상 행은
삭제하지 않는다. 실패는 Actions에서 복구하고 30일 전에 같은 명령을 재실행한다.
이는 비활성·비공개 영상도 YouTube의 30일 보관 한도 안에 정리한다. 최초 배포 뒤에는
정기 작업을 명시적으로 실행해 기존 공개 영상 통계를 채운 뒤 인기순을 확인한다.

0014는 nullable 파생 컬럼 네 개만 추가해 기존 API와 공존한다. 백엔드/키 연결을 먼저
배포하고 프론트엔드를 승격한다. 롤백 시 사용자 데이터는 보존하며 통계 정리 job은 유지한다.
`test_video_browse`는 전체 정렬/페이지, tenant/공개 스냅샷, 미확인/0/만료, 장애/backoff,
삭제 경합, 파생 데이터만 갱신/정리 및 고정 공급자 경계를 검증한다.
공개 탐색 검사는 실제 APIClient·tenant middleware·safe-method guard를 거쳐 실행하며,
GET/HEAD/OPTIONS의 DB 상태 불변과 공급자 호출 0회, 별도 정기 갱신 후 인기순 반영을 검증한다.

공식 근거: [videos.list](https://developers.google.com/youtube/v3/docs/videos/list),
[API 데이터 보관 정책](https://developers.google.com/youtube/terms/developer-policies).

## 원본 확인·실패 복구

정확한 youtube.com/www/m 호스트의 watch·shorts 및 youtu.be만 허용한다.
11자리 ID를 검증하고 공유 추적 파라미터를 제거한다. 사용자 URL을 직접 요청하지 않는다.
고정 YouTube oEmbed HTTPS endpoint, 연결/읽기 timeout, redirect 금지, 응답 64KiB 상한으로
조회한다. 썸네일 주소는 검증된 ID로 만든다. 실패 시 503으로 안내한다.
원본 정보가 없어도 초안 등록은 가능하고 metadata_ready=false로 표시한다.
원본 확인 실패가 공개 상태나 대표 영상을 바꾸지 않는다.

YouTube의 유효 재생 집계는 원본 영상/채널에 귀속되며 매 재생의 조회수 증가를 보장하지
않는다. 사이트는 autoplay나 playVideo 호출 없이 공식 플레이어의 재생 조작을 사용한다.
좋아요·댓글은 YouTube 원본에서 로그인 후 직접 수행한다. 자체 조회/좋아요 수를 만들지 않는다.
임베드가 제한된 영상은 항상 표시되는 원본 링크로 이동할 수 있다.

## 초기 운영 자료와 검증

`seed_godmin_public_videos --actor-id <지정 게시자 ID>`는 godmin 정확한 tenant/두 게시자,
사용자가 제공한 4개 영상의 신규/기존 대상을 읽는 계획이다. `--apply`는 누락된 4개 대상만
공개하며 기존 사용자 작성 초안·공개 설정·대표 선택을 바꾸지 않는다. 메타데이터는 실제로
조회하고 신규 생성에만 사용한다. 최초 showcase가 없을 때만 첫 인터뷰를 대표로 지정한다.
실행은 현재 production source/승인/공유 잠금 절차를 따르며 운영 개인 데이터는 복사하지 않는다.

마이그레이션 0011은 새 테이블/제약만 추가하므로 구버전 API와 공존한다. 롤백해도 새 테이블의
사용자 작성 내용을 삭제하지 않는다. 백엔드 먼저 배포한 뒤 프론트엔드를 승격한다.
0012는 기존 행의 유일성을 유지하면서 삭제되지 않은 행에만 YouTube 유일 제약을 적용하고,
설정 필드를 기본값과 함께 추가한다. 상태 선택지 변경은 컬럼 타입/기존 값을 바꾸지 않는다.
RemoveConstraint/AlterField는 release guard상 contract이므로 호환성을 검토한 정확한 main을
`workflow_dispatch`의 `allow_contract_migrations=true`로 배포한다. 일반 push gate를 우회하지 않는다.
전체 API 교체·검증 이후에만 삭제/재등록 UI를 승격한다. 삭제 사용 후에는 삭제 상태를 모르는
구버전 API로 되돌리지 않고 호환 수정 버전으로 복구한다.
0013은 godmin의 알려진 초기 4개 행 중 creation_content가 원본과 정확히 같은 대상만 잠근다.
초기 자동 소개와 여전히 같은 draft/published summary만 비우고 revision·감사 이벤트를 남긴다.
수정된 제목·소개·분류·공개 상태·대표·다른 tenant는 보존한다. 구버전이 읽는 category 코드는
유지한다. 역방향은 의미를 임의로 복구하지 않는 no-op이며 재실행해도 추가 변경이 없다.
`test_public_videos`는 두 게시자의 저장→reload→공개→초안 수정→재공개→대표 변경→비공개,
중복·응답 유실 재시도·조회 장애·권한·tenant 격리·동시 수정과 URL 요청 경계를 검증한다.
추가로 영역 설정/자유 분류 저장·reload·공개, 삭제·대표 해제·재등록·늦은 재시도,
삭제/수정 경합과 초기 문구의 사용자 수정 보존을 검증한다.
기존 `test_public_resources` 회귀와 PostgreSQL CI를 함께 유지한다. 브라우저의 동일 산출물
실사용은 격리 개발 QA tenant에서 실행하고 최종 tenant/user cleanup zero가 필수다.
