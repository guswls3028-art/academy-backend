# python tools\export_backend_apps_deep_ai_dump.py

# backend 앱 + domains/support 내부 앱까지 AI 덤프
# 결과: 실제 Django app 단위 txt

import os
from pathlib import Path

BACKEND_ROOT = Path(r"C:\academy\apps")
OUT_DIR = Path("ai_dumps_backend")

INCLUDE_EXTS = {".py", ".txt", ".md", ".json", ".yml", ".yaml", ".html"}
EXCLUDE_DIRS = {"__pycache__", ".git", ".idea", ".vscode", "node_modules"}

def dump_folder(folder: Path, prefix: str | None = None):
    name = folder.name if not prefix else f"{prefix}__{folder.name}"
    out_file = OUT_DIR / f"{name}.txt"

    lines = []
    lines.append("=" * 100)
    lines.append(f"# BACKEND APP: {name}")
    lines.append(f"# ROOT PATH: {folder}")
    lines.append("=" * 100)
    lines.append("")

    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]

        for fn in sorted(filenames):
            path = Path(dirpath) / fn
            if path.suffix.lower() not in INCLUDE_EXTS:
                continue

            rel = path.relative_to(folder).as_posix()
            lines.append("\n" + "=" * 90)
            lines.append(f"# FILE: {rel}")
            lines.append("=" * 90)

            try:
                lines.append(path.read_text(encoding="utf-8", errors="replace").rstrip())
            except Exception as e:
                lines.append(f"# [ERROR] {e}")

            lines.append("")

    out_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"[OK] dumped {name}")

def main():
    OUT_DIR.mkdir(exist_ok=True)

    for item in BACKEND_ROOT.iterdir():
        if not item.is_dir() or item.name in EXCLUDE_DIRS:
            continue

        # domains / support 는 내부 앱 분해
        if item.name in {"domains", "support"}:
            for sub in item.iterdir():
                if sub.is_dir() and sub.name not in EXCLUDE_DIRS:
                    dump_folder(sub, prefix=item.name)
        else:
            dump_folder(item)

    print(f"\n[DONE] deep backend AI dumps → {OUT_DIR.resolve()}")

if __name__ == "__main__":
    main()
