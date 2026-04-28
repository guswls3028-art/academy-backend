# OMR 자동채점 실측 acceptance 데이터셋

운영 중 발생한 실 스캔과 정답/마킹 ground truth를 모아 **회귀 측정**과 **임계값 튜닝**의 단일 소스로 사용한다.

## 사용

```bash
cd backend/
PYTHONIOENCODING=utf-8 python tests/omr/acceptance.py \
    --labels tests/omr/acceptance_data/<batch>/labels.json \
    --threshold 0.99 \
    --report tests/omr/acceptance_data/<batch>/report.json
```

종료 코드 0 = recognition accuracy ≥ threshold, 1 = 미달, 2 = 입력 오류.

## 라벨 스키마

`labels.example.json` 참조. 핵심:

- `exam.answer_key` (선택): 정답지. 있으면 AI 점수 vs 실제 점수 비교 추가 출력.
- `scans[].expected_identifier`: 학생이 OMR에 마킹한 8자리 (실제 학생 phone과 다를 수도 있음 — 마킹값 기준).
- `scans[].expected_marks`: 학생이 **실제로 마킹한 답** (정답이 아니라 학생 답안). `null` = 빈칸, `"3,4"` = 이중 마킹.

## 워크플로

1. 운영 시험 1회분 50~100매 스캔본 + 사람이 직접 본 ground truth 라벨링.
2. `<batch>/scans/*.jpg` + `<batch>/labels.json` 작성.
3. acceptance harness 실행 → 99% 미달 시 `report.json`의 `failure_examples`로 실패 패턴 분류.
4. engine config / grader policy 튜닝 → 회귀 재측정.

## 정확도 정의

- **recognition accuracy**: AI 인식 결과(`detected`)가 학생 실제 마킹과 일치한 비율.
  - 빈칸(`null`)은 AI도 `blank`로 판정해야 일치.
  - 이중 마킹(`"3,4"`)은 AI도 동일한 두 답을 detected에 담아야 일치.
- **identifier digit accuracy**: 8자리 식별번호 자리별 일치율.
- **gate**: recognition accuracy ≥ threshold (default 0.99 = 99%).

## 디렉토리 권장

```
acceptance_data/
  README.md
  labels.example.json
  2026_04_test_v1/
    labels.json
    scans/
      scan_001.jpg
      scan_002.jpg
      ...
    report.json   # harness 출력
```

스캔 이미지는 git에 올리지 않는다 (`.gitignore`로 batch 디렉토리 제외 권장 — 단, labels.json은 ground truth로 추적).
