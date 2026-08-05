from __future__ import annotations

import hashlib
import json
import re
import shutil
import unicodedata
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable


SUPPORTED_SUFFIXES = {".hwp", ".hwpx", ".pdf"}
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_WORKBOOK_PATTERN = re.compile(r"(?:워크\s*북|\bWB\b)", re.IGNORECASE)


def _normalize_name(value: str) -> str:
    """Repair legacy Mac ZIP names and return one stable Unicode spelling."""
    raw = str(value or "").replace("\\", "/")
    try:
        repaired = raw.encode("cp437").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        repaired = raw
    return unicodedata.normalize("NFC", repaired)


def _is_archive_metadata(name: str) -> bool:
    parts = PurePosixPath(str(name or "").replace("\\", "/")).parts
    return "__MACOSX" in parts or any(part.startswith("._") for part in parts)


def _category(*values: str) -> str:
    joined = " ".join(_normalize_name(value) for value in values)
    return "workbook" if _WORKBOOK_PATTERN.search(joined) else "exam"


@dataclass(frozen=True)
class SourceOrigin:
    container: str
    archive_entry: str
    display_name: str
    category: str
    size: int


@dataclass
class SourceDocument:
    source_id: str
    sha256: str
    extension: str
    display_name: str
    category: str
    size: int
    origins: list[SourceOrigin]
    extracted_path: str = ""


@dataclass(frozen=True)
class _Candidate:
    origin: SourceOrigin
    sha256: str
    opener: object


def _hash_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _iter_candidates(source_root: Path) -> Iterable[_Candidate]:
    for path in sorted(source_root.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix in SUPPORTED_SUFFIXES:
            relative = path.relative_to(source_root).as_posix()
            size = path.stat().st_size
            with path.open("rb") as stream:
                sha256 = _hash_stream(stream)
            yield _Candidate(
                origin=SourceOrigin(
                    container=relative,
                    archive_entry="",
                    display_name=_normalize_name(path.name),
                    category=_category(relative, path.name),
                    size=size,
                ),
                sha256=sha256,
                opener=lambda path=path: path.open("rb"),
            )
            continue
        if suffix != ".zip":
            continue
        relative_zip = path.relative_to(source_root).as_posix()
        with zipfile.ZipFile(path) as archive:
            for entry in archive.infolist():
                if entry.is_dir() or _is_archive_metadata(entry.filename):
                    continue
                display_name = _normalize_name(PurePosixPath(entry.filename).name)
                entry_suffix = Path(display_name).suffix.lower()
                if entry_suffix not in SUPPORTED_SUFFIXES:
                    continue
                with archive.open(entry) as stream:
                    sha256 = _hash_stream(stream)

                def open_entry(
                    archive_path: Path = path,
                    entry_name: str = entry.filename,
                ) -> BinaryIO:
                    owner = zipfile.ZipFile(archive_path)
                    stream = owner.open(entry_name)
                    original_close = stream.close

                    def close() -> None:
                        try:
                            original_close()
                        finally:
                            owner.close()

                    stream.close = close  # type: ignore[method-assign]
                    return stream

                yield _Candidate(
                    origin=SourceOrigin(
                        container=relative_zip,
                        archive_entry=_normalize_name(entry.filename),
                        display_name=display_name,
                        category=_category(relative_zip, entry.filename),
                        size=entry.file_size,
                    ),
                    sha256=sha256,
                    opener=open_entry,
                )


def build_manifest(
    *,
    source_root: Path,
    output_dir: Path,
    extract: bool = True,
) -> dict:
    source_root = source_root.resolve()
    output_dir = output_dir.resolve()
    if not source_root.is_dir():
        raise ValueError(f"Source root does not exist: {source_root}")

    by_hash: dict[str, SourceDocument] = {}
    opener_by_hash: dict[str, object] = {}
    origin_count = 0
    oversized: list[str] = []
    for candidate in _iter_candidates(source_root):
        origin_count += 1
        if candidate.origin.size > MAX_UPLOAD_BYTES:
            oversized.append(candidate.origin.archive_entry or candidate.origin.container)
        document = by_hash.get(candidate.sha256)
        if document is None:
            extension = Path(candidate.origin.display_name).suffix.lower()
            document = SourceDocument(
                source_id=candidate.sha256[:16],
                sha256=candidate.sha256,
                extension=extension,
                display_name=candidate.origin.display_name,
                category=candidate.origin.category,
                size=candidate.origin.size,
                origins=[],
            )
            by_hash[candidate.sha256] = document
            opener_by_hash[candidate.sha256] = candidate.opener
        document.origins.append(candidate.origin)
        if candidate.origin.category == "workbook":
            document.category = "workbook"

    if oversized:
        raise ValueError("Product upload limit exceeded by: " + ", ".join(oversized))

    documents = sorted(
        by_hash.values(),
        key=lambda item: (item.category, item.display_name, item.sha256),
    )
    if extract:
        extracted_dir = output_dir / "sources"
        extracted_dir.mkdir(parents=True, exist_ok=True)
        for document in documents:
            target = extracted_dir / f"{document.source_id}{document.extension}"
            if not target.exists() or target.stat().st_size != document.size:
                opener = opener_by_hash[document.sha256]
                with opener() as source, target.open("wb") as destination:  # type: ignore[operator]
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
            document.extracted_path = str(target)

    summary = {
        "source_root": str(source_root),
        "origin_count": origin_count,
        "unique_count": len(documents),
        "exact_duplicate_count": origin_count - len(documents),
        "category_counts": {
            category: sum(1 for item in documents if item.category == category) for category in ("exam", "workbook")
        },
        "extension_counts": {
            extension: sum(1 for item in documents if item.extension == extension)
            for extension in sorted(SUPPORTED_SUFFIXES)
        },
        "total_bytes": sum(item.size for item in documents),
        "max_upload_bytes": MAX_UPLOAD_BYTES,
    }
    payload = {
        "summary": summary,
        "documents": [
            {
                **asdict(document),
                "origins": [asdict(origin) for origin in document.origins],
            }
            for document in documents
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return payload


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Inventory and extract real exam/workbook source bundles.")
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--no-extract", action="store_true")
    args = parser.parse_args()

    payload = build_manifest(
        source_root=args.source_root,
        output_dir=args.output_dir,
        extract=not args.no_extract,
    )
    print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
