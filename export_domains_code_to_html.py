# cd c:\academy
# 
# python export_domains_code_to_html.py

import sys
sys.path.pop(0)  # 표준 라이브러리(types 등) 충돌 방지

import os
import html
from pathlib import Path

# ================================
# 대상 폴더 목록
# ================================
TARGET_DIRS = [
    #Path("apps/domains/submissions"),
    #Path("apps/domains/results"),
    #Path("apps/domains/exams"),
    #Path("apps/domains/homework"),
    #Path("apps/domains/ai"),
    Path("apps/shared"),
    #Path("apps/worker/ai"),
    #Path("apps/worker/queue"),
    #Path("apps/support/analytics"),
    Path("apps/core"),
    Path("apps/support/media"),
    Path("libs"),
    Path("apps/worker/media"),
    Path("apps/worker/queue"),

    Path("apps/domains/students"),




]

INCLUDE_EXT = {".py"}
EXCLUDE_DIRS = {
    "__pycache__",
    "migrations",
}

# ================================
# 유틸
# ================================
def should_skip_dir(dirname: str) -> bool:
    return dirname in EXCLUDE_DIRS


def should_include_file(path: Path) -> bool:
    return path.suffix in INCLUDE_EXT


def collect_files(root: Path):
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        for filename in filenames:
            path = Path(dirpath) / filename
            if should_include_file(path):
                files.append(path)
    return sorted(files)


def build_html(root: Path, files):
    sections = []

    for file in files:
        rel_path = file.relative_to(root)
        content = file.read_text(encoding="utf-8", errors="ignore")

        sections.append(f"""
        <section>
            <h2>{html.escape(str(rel_path))}</h2>
            <pre><code>{html.escape(content)}</code></pre>
        </section>
        """)

    return f"""
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>{root.name} Code Browser</title>
<style>
body {{
    background:#0f1115;
    color:#eaeaea;
    font-family:Consolas, monospace;
    padding:24px;
}}
h1 {{ margin-bottom:24px; }}
h2 {{ color:#7dd3fc; font-size:15px; }}
pre {{
    background:#111418;
    padding:16px;
    border-radius:8px;
    overflow-x:auto;
    font-size:13px;
    line-height:1.5;
}}
section {{ margin-bottom:32px; }}
</style>
</head>
<body>
<h1>📦 {root}</h1>
{''.join(sections)}
</body>
</html>
"""


# ================================
# 실행
# ================================
if __name__ == "__main__":
    for root in TARGET_DIRS:
        if not root.exists():
            print(f"[SKIP] 폴더 없음 → {root}")
            continue

        files = collect_files(root)
        output_file = root / f"{root.name}_code.html"
        output_file.write_text(build_html(root, files), encoding="utf-8")

        print(f"[OK] 생성 완료 → {output_file}")
