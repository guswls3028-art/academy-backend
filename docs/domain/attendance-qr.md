# 학생 QR 출석과 클리닉 등원·하원

## 목적과 역할

교직원은 차시 출결 화면에서 강의용 QR, 클리닉 화면에서 장소용 QR을 발급·출력해
문에 게시한다. 학생 본인이 로그인한 학생앱에서 촬영하면 기존 출결 DB에 반영된다.
학부모·교직원의 학생 대리 스캔은 허용하지 않는다. 수업 QR은 매일 재발급하지 않는
강의 단위 표지이며, 클리닉은 특정 장소 또는 학원 전체 장소 표지를 사용한다.

## 저장 경계

- `AttendanceQrPoint`: tenant, class/clinic, 강의 또는 장소, 표시 이름, UUIDv4 토큰,
  사용 여부. 다른 학원의 토큰은 404. 조회·발급·교체·중지는 현재 학원 교직원만 가능하다.
- 수업은 한국 날짜 기준 당일 차시, 활성 강의·수강·차시 등록·학생, 동일 학원/강의의
  관계를 모두 검증한다. 비활성 반은 제외한다. 날짜가 없는 차시는 대상이 아니다.
  날짜만 있는 수업의 시작 시각을 추론하지 않으며 자동 지각 판정은 하지 않는다.
- `Attendance`가 없으면 유효한 차시 등록에 한해서 생성한다. `UNSET → PRESENT`와
  `qr_checked_in_at`만 기록하며 기존 메모·등원 예정은 보존한다. PRESENT/LATE 반복은
  이미 기록됨을 반환한다. 교사가 입력한 결석/영상/보강/퇴원 등 다른 상태는 409로
  보호하며 재등록을 수행하지 않는다. 기존 TODO 대상 재계산 경계를 재사용한다.
- 클리닉은 현재 학생의 booked/attended 참가 기록과 같은 학원의 일정만 허용한다.
  QR에 장소가 있으면 일정 장소가 정확히 일치해야 한다. 확정 예약의 실제 시작
  60분 전부터 종료 3시간 후까지 이용하며 시간 범위 예약과 자정 경계를 지원한다.
  대기·취소·거절·결석 또는 예약 없는 학생의 자동 등록은 하지 않는다.
- 등원은 기존 lifecycle `change_participant_status`, 하원은 `checkout_participant`를
  사용한다. 하원에는 등원 기록이 필요하다. 반복 촬영은 입·퇴실을 토글하지 않으며
  하원 시각·등원 시각을 덮어쓰지 않는다. `completed_at`, 학습 결과와 ClinicLink는
  변경하지 않는다. 출석 저장만 수행하며 메시지 발송은 기존 교직원 발송 흐름이 소유한다.
- 학생 → QR → 수강/참가자 잠금으로 같은 학생의 요청을 직렬화한다. 잠금 뒤 관계와
  상태를 재확인한다. 교체/중지도 같은 QR 행을 잠근다. 서버 시각을 사용한다.

## API와 복구

교직원 `GET/POST /api/v1/lectures/attendance-qr/`, `PATCH {id}/`(표시 이름·사용 여부),
`POST {id}/rotate/`. class에는 `lecture_id`, clinic에는 `location`을 지정한다.
빈 location은 학원 전체 장소를 뜻한다. 발급 후 대상 범위는 바꿀 수 없으며 새 표지를
만든다. 교체는 이전 토큰을 즉시 폐기한다. 중지/교체는 이미 저장된 출결을 삭제하지 않는다.

학생 `POST /api/v1/student/attendance/qr/resolve/ {token}`은 표지와 가능한 차시/예약을
반환한다. `POST .../record/ {token,target_id,action}`은 class 차시 ID 또는 clinic 참가자
ID와 `check_in`/`check_out`을 받는다. 학생 ID는 입력으로 받지 않고 인증에서 결정한다.
성공 응답은 `detail, already_recorded, kind, target_id, status, recorded_at,
checked_in_at, checked_out_at`. 반복 요청은 기존 시각을 반환한다. 분당 30회 제한이다.
네트워크 응답을 잃으면 같은 요청을 재시도하여 저장 여부를 확인할 수 있다.

학생 출결 요약과 교직원 출결 serializer는 읽기 전용 `qr_checked_in_at`을 추가한다.
클리닉 소비 화면은 기존 checked_in_at/checked_out_at을 그대로 사용한다.
프런트엔드 흐름과 카메라 복구는 [QR 화면 계약](https://github.com/guswls3028-art/academy-frontend/blob/main/docs/ATTENDANCE-QR.md)이 소유한다.

## 호환성과 검증

0006은 신규 표와 nullable 시각 컬럼만 추가하는 확장 migration이다. 기존 출결과
클리닉 기록을 보정/삭제하지 않는다. 서버와 migration을 먼저 배포한 뒤 화면을 배포한다.
구 클라이언트는 추가 필드를 무시하며 QR 기능 배포로 강제 새로고침하지 않는다.
인쇄된 고정 QR은 사진 공유가 가능하므로 물리적인 현장 존재를 증명하지는 않는다.
노출된 표지를 교체/중지할 수 있으며 출석은 본인 계정과 등록/예약 경계로 제한한다.

`apps/domains/attendance/tests/test_qr_attendance.py`는 발급/교체/중지, 출석 저장과
재조회/중복, 역할·학원·날짜·등록·수동 상태 보호, 클리닉 등원/하원·미완료 보존,
취소/대기/장소/시간과 자정 경계를 검증한다. DB 잠금 보장은 PostgreSQL 검증이 필요하다.
실사용은 격리 development의 합성 학생·강의·예약으로 인쇄→인식→저장→양 역할 재조회와
cleanup zero를 확인한다. mock E2E는 실제 카메라 하드웨어나 운영 저장의 증거가 아니다.
