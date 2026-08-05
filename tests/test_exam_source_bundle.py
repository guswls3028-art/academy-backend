from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

from scripts.exam_source_bundle import build_manifest


def test_manifest_ignores_macos_metadata_and_keeps_duplicate_provenance(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "sources"
    source_root.mkdir()
    document = b"real hwp bytes"
    (source_root / "문제.hwp").write_bytes(document)
    with zipfile.ZipFile(source_root / "Archive.zip", "w") as archive:
        archive.writestr("문제.hwp", document)
        archive.writestr("__MACOSX/._문제.hwp", b"apple-double")
        archive.writestr("ignore.txt", b"not a product source")

    payload = build_manifest(
        source_root=source_root,
        output_dir=tmp_path / "output",
    )

    assert payload["summary"] == {
        "source_root": str(source_root.resolve()),
        "origin_count": 2,
        "unique_count": 1,
        "exact_duplicate_count": 1,
        "category_counts": {"exam": 1, "workbook": 0},
        "extension_counts": {".hwp": 1, ".hwpx": 0, ".pdf": 0},
        "total_bytes": len(document),
        "max_upload_bytes": 50 * 1024 * 1024,
    }
    item = payload["documents"][0]
    assert item["sha256"] == hashlib.sha256(document).hexdigest()
    assert item["display_name"] == "문제.hwp"
    assert len(item["origins"]) == 2
    assert Path(item["extracted_path"]).read_bytes() == document


def test_workbook_category_uses_container_or_filename(tmp_path: Path) -> None:
    source_root = tmp_path / "sources"
    workbook_dir = source_root / "워크북"
    workbook_dir.mkdir(parents=True)
    (workbook_dir / "자료.hwp").write_bytes(b"workbook-a")
    (source_root / "Algebra WB 3.hwpx").write_bytes(b"workbook-b")
    (source_root / "Exam.pdf").write_bytes(b"exam")

    payload = build_manifest(
        source_root=source_root,
        output_dir=tmp_path / "output",
    )

    assert payload["summary"]["category_counts"] == {
        "exam": 1,
        "workbook": 2,
    }
