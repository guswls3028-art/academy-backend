# Ymath 실자료 원본 전수 검증

Ymath 선생님이 실제 사용하는 시험지·워크북 원본을 운영 데이터와 분리된
개발 테넌트에서 시험 생성, 문항 분리, 검수, 채점, 통합 오답노트까지 검증하는
절차다. 작은 합성 fixture 성공을 실사용 합격으로 대신하지 않는다.

## 안전 경계

- 실행 대상 tenant code는 `qa-ymath-realuse-`로 시작해야 한다.
- 시나리오 생성 명령은 `academy_api_development`와
  `academy-development-*` R2 조합, 또는 SQLite/test DB와 `test-*` R2
  조합에서만 동작한다. production DB/R2 형태에서는 즉시 실패한다.
- 실제 Ymath의 학생, 학부모, 성적, 연락처, 결제 데이터는 복제하지 않는다.
  프로그램의 안전한 feature flag와 UI 설정만 읽고, 교사·학생·강의·회차는
  합성 식별자로 새로 만든다.
- 시나리오 프로그램은 생성일로부터 365일 동안만 활성 구독 상태를 갖는다.
  만료일이 없는 `all` plan은 실제 API가 `subscription_expired`로 차단하므로
  유효한 기간과 `cancel_at_period_end=false`를 함께 설정하고 출력 JSON에
  만료일을 기록한다.
- 원본 HWP/HWPX/PDF는 읽기 전용 입력이다. tenant 전용 R2 자산과 검수 후보만
  생성하며 자동으로 문항 정본을 승인하지 않는다.
- API 실행은 SSM-only development API에 연결한 loopback URL만 허용한다.

## 원본 묶음과 구조 감사

```powershell
$backend = "C:\academy\backend"
$source = "C:\academy\테넌트별자료양식\ymath"
$artifact = "C:\academy\_artifacts\ymath-real-scenario-20260805"

python "$backend\scripts\exam_source_bundle.py" `
  --source-root $source `
  --output-dir $artifact

python "$backend\scripts\exam_source_hwp_qa.py" `
  --manifest "$artifact\manifest.json" `
  --output-dir "$artifact\qa" `
  --preview-all
```

묶음 도구는 ZIP의 AppleDouble/metadata를 제외하고 SHA-256 중복을 합치되 모든
원래 위치를 `origins`에 남긴다. 각 고유 원본의 확장자, 용량, 시험/워크북 분류와
50MB 업로드 계약을 기록한다. HWP/HWPX 감사는 모든 번호 미주와 원본 그림뿐
아니라 같은 번호의 본문 문자·EqEdit 수식·삽화도 읽고 각 번호의 깨끗한 본문
문제, 안전한 본문·수식 해설, raw picture-control attachment 미리보기를 각각
만든다. `safe_explanation_count`가 번호 수와 같아야 하며 첫·중간·마지막을 직접
열어 번호가 일치하는지 확인한다. raw attachment는 표지나 이웃 문항을 포함할 수
있으므로 기본 해설 합격 근거로 쓰지 않는다. 미주 해설 상단 크롭을 문제
미리보기로 쓰면 실패다.

2026-08-05 제공 묶음의 재현 기준선은 고유 원본 156개(시험 137, 워크북 19),
HWP 114, HWPX 19, PDF 23이다. HWP/HWPX 133개는 구조 읽기 오류 0건이며,
본문 문제와 미주 해설을 모두 요구하는 2026-08-07 기준으로 106개는 단일
문제+해설 파일 후보를 만들 수 있고 27개는 깨끗한 문제 PDF가 추가로 필요하다.
그중 26개는 일부 미주 원본 그림이 빠졌고, `7c71968858931fff`는 미주 22개 중
본문 7번 문제를 재현하지 못했다. 이 27개를 성공으로 세면 안 된다. 전수 합계는
미주 번호 3,537개, 해설 원본 3,204개, 본문 문제 3,536개다.
짝지은 문제 PDF에서 1번만 빠지고 2번 이후가 연속이면 원본 PDF의 같은 열
`1.`·`2.` 앵커로 복구되는지 확인한다. 2026-08-05 기준
`b70fd8b2883ffcc1`은 23개(2~24)에서 24개(1~24)로 복구되어야 하며, 다른 열의
`1.` 표시는 복구 근거로 사용하지 않는다.

## 격리 시나리오 생성

후보 이미지를 persistent development runtime에 배포한 뒤 API 컨테이너에서
다음을 실행한다. 비밀번호는 명령줄이나 결과 JSON에 넣지 않고 일회성 환경 변수로
전달한다.

```powershell
$env:YMATH_REALUSE_SCENARIO_PASSWORD = '<ephemeral-secret>'
python manage.py setup_ymath_realuse_scenario `
  --tenant-code qa-ymath-realuse-20260805 `
  --student-count 6 `
  --session-count 24 `
  --reset
```

출력 JSON의 `tenant_code`, 교사 ID, 학생·강의·회차 ID와 개수를 보존한다. reset은
정확히 같은 `qa-ymath-realuse-*` tenant만 지우고 다시 만든다.

## API 전수 실행

먼저 `scripts/v1/connect-api-development.ps1`로 loopback SSM tunnel을 연다.
PDF가 추가로 필요한 HWP/HWPX는 `source_id`를 깨끗한 문제 PDF 절대 경로에
매핑한 JSON을 준비한다. 문제와 답 표시가 섞인 PDF는 깨끗한 문제지로 인정하지
않는다.

```powershell
python scripts/ymath_realuse_scenario.py `
  --manifest "$artifact\manifest.json" `
  --hwp-qa "$artifact\qa\hwp-qa.json" `
  --scenario "$artifact\scenario.json" `
  --pairings "$artifact\pairings.json" `
  --output "$artifact\realuse-result.json" `
  --api-base-url http://127.0.0.1:18000 `
  --tenant-code qa-ymath-realuse-20260805
```

러너는 실제 `/exams/`, `/homeworks/`, `/homeworks/{id}/source-exam/`,
`/exams/pdf-extract/`, `/jobs/{id}/`, `/exams/{id}/segmentation-review/` 계약을
사용한다. 시험·워크북 생성과 job 제출 직후 상태를 원자적으로 저장하므로 중단 후
같은 결과 파일로 재실행할 수 있다. `review_required`인 항목은 재실행하지 않는다.
각 상품 제목에는 `source_id`를 넣어 유일하게 만들며, POST 응답이 끊겼더라도 같은
회차의 정확한 제목을 한 건만 찾아 이어받는다. 업로드 응답 유실 뒤에는 시험의
`segmentation_status`를 폴링해 이미 접수된 job을 재제출하지 않는다. 같은 제목이
여러 건이면 자동 선택하지 않고 실패 폐쇄한다. 재실행에서 완료 항목을 제외해도
각 원본의 회차는 전체 plan의 원래 순번으로 계산해 바뀌지 않으며, 생성 응답 단절
뒤에는 최대 30초 동안 commit 가시성을 기다린다.
워크북 `source-exam` 생성이 PostgreSQL의 nullable outer join `FOR UPDATE` 오류를
내면 제품 실패다. `Homework` 본행만 잠그는 계약을 배포한 뒤 같은 체크포인트를
재실행해 기존 워크북을 중복 생성하지 않고 이어져야 한다.

## 합격 기준

- manifest의 모든 고유 원본이 결과에 한 번씩 나타난다.
- 깨끗한 문제지가 없어 자동 확정할 수 없는 원본도 실제 API/worker를 통과시킨다.
  이 항목은 `source_remediation_required`로 기록되고 제품은
  `conversion_required`로 실패 폐쇄해야 한다. 실행에서 생략하거나 성공으로 세지 않는다.
- PDF/이미지, 통합 HWP/HWPX, 문제 PDF+해설 HWP/HWPX 세 경로가 모두 실제
  API를 통과한다.
- 문제 PDF+해설 HWP 경로는 그림이 없는 미주도 ParaText·EqEdit 수식·BinData
  삽화를 번호별 검수 이미지로 재현해야 한다. 2026-08-05 짝 자료 6개는 각각
  24개 미주가 있어 총 144개 해설 후보가 생겨야 하며, 그림 수만 센 8/3/8/4/9/17
  건을 성공으로 보면 안 된다. 첫·중간·마지막 조판 이미지를 직접 열어 한글 문장,
  분수·근호·극한, 원본 삽화가 읽히는지 확인한다.
- job 성공과 별개로 시험 상태가 `review_required`이고 예상 문항 수와 proposal
  수가 일치해야 한다. `conversion_required`, 빈 proposal, 일부 해설 누락,
  timeout은 별도 실패다.
- 체크포인트 재개 시 성공 제품은 건너뛰고 동일 제목 제품을 재사용한다. 다만
  `question_count_mismatch`, `teacher_explanation_coverage_incomplete`처럼 소스 분석
  품질이 실패한 항목은 기존 시험을 중복 생성하지 않고 같은 시험에 소스를 다시
  제출해 현재 worker로 재분석한다. 과거 job/review만 다시 읽어 성공으로 세면 안 된다.
- 단일 HWP/HWPX는 `problem_visual_count`와 `safe_explanation_count`가 번호별로
  모두 일치해야 한다. 첫·중간·마지막 및 도형·선택지 표본에서 문제 이미지는
  본문 원문만, 기본 해설은 같은 번호의 ParaText·EqEdit만 보여야 한다. raw
  picture-control attachment에 표지·이웃 문항이 섞여 있어도 자동 선택하지 않고
  검수 화면에서만 비교한다. 문제 쪽에 정답 색칠·손필기 풀이가 보이면 자동
  합격시키지 않는다. 과거 `hwp_endnote` 검수 후보에만 남은 크롭 슬라이더는
  호환용이며 신규 성공 기준이 아니다.
- 교사 검수 결정 없이 승인 API를 호출하지 않는다. 승인 뒤에는 문항 번호,
  포함/제외, 원본 해설 연결 수를 다시 읽는다.
- 시험과 워크북에서 각각 2명 이상에게 O/X/복습을 저장하고 새로고침 후
  round-trip을 확인한다. 선택한 시험+워크북 문항으로 PDF와 HWPX 오답노트를
  만들고 다운로드·페이지·원본 필기/수식/도형을 확인한다.
- 결과에는 성공 수뿐 아니라 보완 PDF 필요, 문항 수 불일치, 해설 미연결,
  수동 크롭 필요 항목을 모두 남긴다.

관련 제품 계약은 [시험 생성·채점·오답노트](../../domain/exam-grading.md)와
[과제·워크북 채점](../../domain/homework-grading.md)을 따른다.
