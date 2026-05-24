# apps/worker/ai/problem/prompt.py
BASE_PROMPT = """
다음은 시험 문제의 OCR 결과입니다.
아래 텍스트를 기반으로 문제 정보를 JSON 형식으로 추출하세요.

요구사항:
1) 문제 본문 (body)
2) 선택지 (choices): 없으면 빈 배열
3) 정답 (answer): 명시된 정답이 없으면 AI가 추론, 추론 불가 시 null
4) 난이도 (difficulty): 1~5 정수로 추정
5) 태그 (tag): 수학/과학/국어 등 간단한 분류
6) 문제 요약 (summary)
7) 해설 (explanation): 간단명료하게

출력은 반드시 JSON 형식만 사용하세요. 다른 텍스트는 포함하지 마세요.

출력 형식 예시:
{
  "body": "...",
  "choices": ["A...", "B...", "C...", "D..."],
  "answer": "C",
  "difficulty": 3,
  "tag": "수학",
  "summary": "...",
  "explanation": "..."
}

OCR 텍스트:
\"\"\"
{ocr_text}
\"\"\"
"""


PACKAGE_PROMPT = """
다음 소스 자료를 바탕으로 학원 검수용 문제와 정답/해설 초안을 JSON으로 만드세요.

중요 원칙:
1) 저작권이 있는 교재 원문을 그대로 장문 복제하지 말고, 개념과 풀이 구조 중심으로 재구성합니다.
2) mode가 copy이면 원본 문항 구조를 최대한 보존해 깔끔하게 정리합니다.
3) mode가 same-type/trap/concept이면 같은 개념의 후보를 만듭니다. variant_count를 넘기지 않습니다.
4) 해설은 교과서 개념 중심으로 짧지만 충분히 자세하게 씁니다.
5) 함정/오답 유도 문항은 왜 헷갈리는지 한 문장을 덧붙입니다.
6) 정답을 확신할 수 없으면 "검수 필요"라고 씁니다.
7) 출력은 JSON 객체만 반환합니다. 코드블록, 설명문, 마크다운을 붙이지 마세요.

출력 형식:
{
  "questions": [
    {
      "prompt": "문제 본문",
      "choices": ["① ...", "② ..."],
      "answer": "정답 또는 검수 필요",
      "explanation": "짧은 해설",
      "source_index": 1,
      "variant_index": 1
    }
  ]
}

조건:
- 과목: {subject}
- 생성 방식: {mode}
- 후보 수: {variant_count}
- 최대 문항 수: {max_questions}
- 해설 기준: {note_policy}

소스 자료:
\"\"\"
{source_text}
\"\"\"
"""
