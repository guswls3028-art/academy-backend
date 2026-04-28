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
