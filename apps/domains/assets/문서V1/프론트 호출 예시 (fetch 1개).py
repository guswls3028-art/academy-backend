async function downloadOmr(questionCount: 10 | 20 | 30, logoFile?: File) {
  const fd = new FormData();
  fd.append("question_count", String(questionCount));
  if (logoFile) fd.append("logo", logoFile);

  const res = await fetch("/api/v1/assets/omr/objective/pdf/", {
    method: "POST",
    body: fd,
    credentials: "include", // 쿠키 인증이면 유지
  });

  if (!res.ok) {
    const txt = await res.text();
    throw new Error(txt);
  }

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  window.open(url);
}

수동 QA 체크리스트 (PDF 확인 포인트)

공통

PDF가 벡터로 보이는지 확인 (확대해도 원/선이 깨지지 않아야 함)

흑백 출력 기준으로 대비가 충분한지 확인

레이아웃(3단)

A4 가로로 생성되는지

영역1(좌측): 상단 로고 / 하단 식별자 존재

영역2/3: 객관식 문항만 존재

좌측/우측 정렬 철학

“휴대폰번호(010 제외)” 라벨은 좌측

식별자 버블은 우측 정렬로 붙어 있는지

문항 번호는 좌측, 5개 버블은 우측 정렬로 붙어 있는지

문항 수 버전

question_count=10: 문항 간격이 가장 넓은지

question_count=20: 중간

question_count=30: 가장 촘촘하지만 여전히 널널한지

입력 검증

question_count 누락 → 400

question_count=15 → 400

logo에 텍스트 파일 업로드 → 400

logo content-type이 image/* 아닌 경우 → 415