# Fix INFRA_VERIFICATION_SCRIPTS.md 용도 row
p = "docs/INFRA_VERIFICATION_SCRIPTS.md"
with open(p, "r", encoding="utf-8") as f:
    s = f.read()
# Normalize curly quotes to ASCII
s = s.replace("\u201c", '"').replace("\u201d", '"')
old = '| **용도** | Video 배포 후 "원테이크" 검증 | 정기 점검·자동 수정 |'
new = "| **용도** | Video 배포 직후 검증 | 정기 점검·자동 수정 |"
s = s.replace(old, new)
with open(p, "w", encoding="utf-8") as f:
    f.write(s)
print("ok")
