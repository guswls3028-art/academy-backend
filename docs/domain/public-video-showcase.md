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
이전 공개 스냅샷을 보존한다. 공개 API는 published 상태만 반환한다. 삭제 API는 없다.

PublicVideoShowcase는 tenant별 대표 영상 0~1개다. 대표 지정과 공개, 비공개 시 대표 해제는
하나의 트랜잭션이다. 비공개 대표는 홈페이지의 기존 강사 프로필로 복귀한다.
모든 쓰기는 User → TenantMembership → PublicResourceBoardAccess 순으로 잠근다.
tenant의 access 행으로 편집자 간 쓰기를 직렬화하고 video/showcase revision으로
오래된 화면의 덮어쓰기를 409로 거절한다. 권한 회수와 동일 잠금 순서를 유지한다.
공개/비공개 요청에는 두 revision이 필요하다. 감사를 위한 PublicVideoEvent에
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
| POST `manage/<uuid>/publish/`, `unpublish/` | 두 revision 검증 후 공개/비공개 |
| GET/PATCH `manage/showcase/` | 대표 ID/revision 조회 및 공개 영상만 대표 지정 |

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
`test_public_videos`는 두 게시자의 저장→reload→공개→초안 수정→재공개→대표 변경→비공개,
중복·응답 유실 재시도·조회 장애·권한·tenant 격리·동시 수정과 URL 요청 경계를 검증한다.
기존 `test_public_resources` 회귀와 PostgreSQL CI를 함께 유지한다. 브라우저의 동일 산출물
실사용은 격리 개발 QA tenant에서 실행하고 최종 tenant/user cleanup zero가 필수다.
