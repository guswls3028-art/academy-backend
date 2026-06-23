from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from apps.domains.tools.problem_studio.transfer_documents import build_transfer_package


class LocalUpload:
    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.size = path.stat().st_size
        self._handle = path.open("rb")

    def read(self) -> bytes:
        return self._handle.read()

    def seek(self, offset: int) -> None:
        self._handle.seek(offset)

    def close(self) -> None:
        self._handle.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert Problem Studio fixture sources into Hangul-compatible transfer docs.")
    parser.add_argument("--input-dir", default=r"C:\academy\문제생성기자료")
    parser.add_argument("--output-dir", default=r"C:\academy\_artifacts\problem-generator-output")
    parser.add_argument("--title", default="화학2 테스트자료")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    uploads = [LocalUpload(path) for path in sorted(input_dir.iterdir(), key=lambda p: p.name) if path.is_file()]
    try:
        package = build_transfer_package(payload={"title": args.title}, source_files=uploads)
    finally:
        for upload in uploads:
            upload.close()

    package_path = output_dir / package.filename
    package_path.write_bytes(package.data)
    with zipfile.ZipFile(package_path) as zf:
        package_files = zf.namelist()

    summary = {
        "package": str(package_path),
        "document_count": len(package.documents),
        "warning_count": len(package.warnings),
        "review_file_count": package.review_file_count,
        "structured_item_count": package.structured_item_count,
        "ocr_candidate_count": package.ocr_candidate_count,
        "quality_level": package.quality_level,
        "package_file_count": len(package_files),
        "package_files": package_files,
        "warnings": package.warnings,
        "documents": [
            {
                "filename": doc.filename,
                "source_name": doc.source_name,
                "kind": doc.kind,
                "page_count": doc.page_count,
                "image_count": doc.image_count,
                "text_chars": doc.text_chars,
                "warning": doc.warning,
            }
            for doc in package.documents
        ],
    }
    summary_path = output_dir / "problem_studio_transfer_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
