RESULTS 도메인 단일진실(SSOT) 봉인 문서
📌 문서 목적 (READ FIRST)

이 문서는 results 도메인의 설계·책임·경계를 영구적으로 고정하기 위한 봉인 문서다.

이 문서가 존재하는 한:

❌ 구조 재설계 금지

❌ 책임 이동 금지

❌ “편의상” 로직 추가 금지

모든 변경은 이 문서와 충돌하지 않아야 하며,
충돌 시 코드가 아니라 문서가 정답이다.

🎯 최종 결론 (한 줄 요약)

이 프로젝트의 results 도메인은 이미 “대기업 운영 레벨”로 완성되었으며,
문제는 설계가 아니라 과거 잔존 코드와 집계 책임 혼재였다.
본 문서는 그 혼재를 영구적으로 차단한다.

🧭 전체 도메인 단일진실 지도 (SSOT MAP)
1️⃣ Identity & Ownership (절대 고정)
Student

실존 인물

로그인(User)과 선택적으로 연결

❌ 결과의 직접 주체 아님

❌ 시험/통계 FK 금지

Enrollment ⭐⭐⭐ (핵심)

모든 학습/시험/결과/통계의 유일한 주체

(student, lecture) 단일

Results FK는 무조건 enrollment_id

Student는 언제나 간접 참조만 허용

👉 Results는 Student를 절대 직접 참조하지 않는다.

2️⃣ Lecture / Session 도메인 (운영 단위)
Lecture

교육 상품

여러 Session 보유

❌ 시험/결과 계산 책임 없음

Session

운영 단위 (차시)

Lecture FK

Exam과 N:M

“이 차시에 시험이 있었는가?”
→ Result / Progress로 판단

👉 lectures 도메인은 결과를 계산하지 않는다.

3️⃣ Exam 도메인 (출제 단위)

Exam은 template / regular

시험 정의 / 자산 / 정답의 SSOT

Session ↔ Exam = N:M

❌ 결과/통계 책임 없음

4️⃣ Submission → Results (가장 중요한 축)
Submission

답안의 SSOT

상태 머신:

CREATED
  → ANSWERS_READY
    → GRADING
      → DONE

Results ⭐⭐⭐

단 하나의 결과 진실

구성 요소:

ExamAttempt (시도)

ResultFact (append-only, 원시 로그)

ResultItem (문항 스냅샷)

Result (대표 결과)

재시험 / 재채점 / 대표 attempt 교체 / 통계
→ 전부 이 구조로 커버

👉 Results는 사실만 기록한다.
계산·해석·판단은 하지 않는다.

🧱 Aggregation Layer (해석의 유일한 장소)
apps/domains/results/aggregations/
├─ session_results.py
├─ lecture_results.py
└─ global_results.py

역할

집계 / 통계 / 판단의 유일한 책임

SQL, aggregation, business rule 허용

View / Model / Serializer는 절대 계산 금지

원칙

Results = write-only facts

Aggregations = read-only interpretation

🚫 금지 사항 (영구 봉인)

아래는 어떤 이유로도 금지된다:

❌ Result에 session_id FK 추가

❌ Session.exam FK 부활

❌ Results에서 Student 직접 참조

❌ View / Serializer / Model에 집계 로직 작성

❌ “편의상” 계산 로직 추가

❌ Aggregation 로직을 다른 도메인으로 이동

⚠️ 과거 문제의 정체 (재발 방지용 기록)
문제는 이것뿐이었다

lectures 도메인에 남아 있던 구버전 시험 FK 사고

Results에 집계 책임이 섞여 있던 상태

Aggregation Layer 부재

👉 설계 자체는 처음부터 정답이었다.

✅ 현재 상태 선언 (FINAL)

✅ Results 도메인: 완전 고정

✅ Aggregation Layer: 단일진실

✅ 운영 사고 가능성: 구조적으로 0

✅ 백엔드 재방문 필요성: 없음

이후 개발은 프론트엔드 작업만 진행한다.
백엔드 변경이 필요하다면, 이 문서를 먼저 수정하고
그 다음에 코드를 수정한다.

🔒 봉인 선언

이 문서 이후로:

“results 도메인은 더 이상 실험 대상이 아니다.
이미 운영 기준을 통과한 ‘완성품’이다.”

— END —