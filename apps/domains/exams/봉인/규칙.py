봉인 포인트 정리 (어떤 사고를 막기 위해 무엇을 봉인했는지)

(권한 사고 차단)

Exam/Sheet/Question/AnswerKey/AutoQuestions/Asset의 생성·수정·삭제를 전부 Teacher/Admin만 가능하게 봉인.

(단일진실 우회 차단)

template resolver를 resolve_template_exam()로 통일하고, AnswerKey/Asset/Questions 조회는 regular → template로 resolve 강제.

(운영 사고 차단: 템플릿 변경으로 시험 사고)

template이 derived regular를 하나라도 가지면 구조 변경(Sheet/Question/AnswerKey/Asset/AutoQuestions) 전면 금지

즉, “이미 다른 강의에서 재사용된 템플릿”은 문제/정답/자산 교체 불가로 봉인.

(프론트 실수 방어)

ExamViewSet.update/partial_update에서 forbidden field 들어오면 즉시 400

(DRF가 fields 밖 데이터를 “무시”해버리는 케이스로 인해 사고나는 걸 차단)

“이제 하지 말아야 할 것” 리스트 (시니어 기준 금지 사항)

template exam이 regular에 의해 사용 중인 상태에서
문제/정답/자산을 바꾸는 요구사항을 받으면, exams에서 억지로 풀지 말 것.

“운영 중 시험 문제 수정” 요구를 exams에서 처리하지 말 것.

AnswerKey/Questions/Assets를 regular에 달아달라는 요구는 무조건 거절(단일진실 위반).

봉인 규칙을 프론트에서 우회하려는 시도(예: PATCH로 hidden field 보내기)를 허용하지 말 것.

최종 커밋 메시지 (출시 직전 기준)

seal(exams): lock template structural edits once derived regular exists; enforce teacher-only mutations

seal(exams): enforce immutable update fields and unify template resolver usage

seal(exams): wire student available exams endpoint and harden query validation