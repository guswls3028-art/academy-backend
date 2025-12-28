# tools/export_libs_plain.py
# libs 전체를 AI 전송용 "순수 코드 텍스트"로 덤프

import os
from pathlib import Path

ROOT = Path(r"libs")
OUT = ROOT / "libs_ai_dump.txt"

INCLUDE_EXTS = {
    ".py", ".ps1", ".sh", ".txt", ".md", ".json", ".yml", ".yaml",
}

EXCLUDE_DIRS = {
    "__pycache__", ".git", ".idea", ".vscode", "node_modules",
}

def main():
    lines = []

    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

        for fn in sorted(filenames):
            path = Path(dirpath) / fn
            if path.suffix.lower() not in INCLUDE_EXTS:
                continue

            rel = path.relative_to(ROOT).as_posix()
            lines.append("\n" + "=" * 80)
            lines.append(f"# FILE: {rel}")
            lines.append("=" * 80)

            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                lines.append(f"# [ERROR] {e}")

            lines.append(text.rstrip())
            lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] dumped to {OUT}")

if __name__ == "__main__":
    main()
