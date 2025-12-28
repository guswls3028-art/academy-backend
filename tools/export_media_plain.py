# tools/export_media_plain.py
# apps/support/media 폴더를 AI 전송용 "순수 코드 텍스트"로 덤프

import os
from pathlib import Path

ROOT = Path(r"apps/support/media")
OUT = Path(r"apps/support/media/media_ai_dump.txt")

INCLUDE_EXTS = {
    ".py",
    ".html",
    ".json",
    ".md",
}

EXCLUDE_DIRS = {
    "__pycache__", ".git", ".idea", ".vscode",
}

def main():
    lines = []

    for dirpath, dirnames, filenames in os.walk(ROOT):
        # exclude dirs
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
                continue

            lines.append(text.rstrip())
            lines.append("")  # spacing

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] dumped to {OUT}")

if __name__ == "__main__":
    main()
