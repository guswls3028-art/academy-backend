from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from apps.domains.tools.problem_studio.transfer_documents import TRANSFER_MAX_UPLOAD_BYTES


SOURCE_ARCHIVE_SCHEMA = "problem-studio-transfer-source-archive/v1"
SOURCE_ARCHIVE_MANIFEST = "00_source_manifest.json"
SOURCE_ARCHIVE_MAX_FILES = 40
SOURCE_ARCHIVE_MAX_TOTAL_BYTES = 512 * 1024 * 1024


def _safe_filename(value: str, *, default: str = "source") -> str:
    name = Path(value or default).name
    name = re.sub(r"[\\/:*?\"<>|]+", "-", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    return name[:120] or default


def _safe_archive_ext(filename: str) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    if not suffix or not suffix.isalnum() or len(suffix) > 10:
        return "bin"
    return suffix


@dataclass
class ArchivedSourceFile:
    name: str
    path: Path
    size: int
    content_type: str = "application/octet-stream"
    _handle: Any = None

    def _open(self):
        if self._handle is None or self._handle.closed:
            self._handle = self.path.open("rb")
        return self._handle

    def read(self, *args):
        return self._open().read(*args)

    def seek(self, *args):
        return self._open().seek(*args)

    def close(self) -> None:
        if self._handle is not None and not self._handle.closed:
            self._handle.close()


def build_source_archive(source_files: list[Any]):
    if len(source_files) > SOURCE_ARCHIVE_MAX_FILES:
        raise ValueError(f"최대 {SOURCE_ARCHIVE_MAX_FILES}개 파일까지 처리할 수 있습니다.")

    archive = tempfile.TemporaryFile()
    manifest_files: list[dict[str, Any]] = []
    total_size = 0

    with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_STORED, allowZip64=True) as zf:
        for index, uploaded in enumerate(source_files):
            name = _safe_filename(str(getattr(uploaded, "name", "") or f"source-{index + 1}"))
            content_type = str(getattr(uploaded, "content_type", "") or "application/octet-stream")
            archive_name = f"files/{index:03d}_{Path(name).stem}.{_safe_archive_ext(name)}"
            info = zipfile.ZipInfo(archive_name)
            info.compress_type = zipfile.ZIP_STORED
            info.date_time = (1980, 1, 1, 0, 0, 0)

            try:
                uploaded.seek(0)
            except Exception:
                pass

            written = 0
            with zf.open(info, mode="w") as target:
                while True:
                    chunk = uploaded.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > TRANSFER_MAX_UPLOAD_BYTES:
                        raise ValueError(f"{name} 파일 크기가 너무 큽니다.")
                    total_size += len(chunk)
                    if total_size > SOURCE_ARCHIVE_MAX_TOTAL_BYTES:
                        raise ValueError("전체 업로드 크기가 너무 큽니다.")
                    target.write(chunk)

            try:
                uploaded.seek(0)
            except Exception:
                pass

            manifest_files.append({
                "name": name,
                "archive_name": archive_name,
                "size": written,
                "content_type": content_type,
            })

        zf.writestr(
            SOURCE_ARCHIVE_MANIFEST,
            json.dumps({
                "schema": SOURCE_ARCHIVE_SCHEMA,
                "file_count": len(manifest_files),
                "total_size": total_size,
                "files": manifest_files,
            }, ensure_ascii=False, indent=2),
        )

    archive.seek(0)
    return archive, manifest_files


@contextmanager
def source_files_from_archive(archive_path: str | Path) -> Iterator[list[ArchivedSourceFile]]:
    root = Path(tempfile.mkdtemp(prefix="problem-studio-transfer-"))
    files: list[ArchivedSourceFile] = []
    try:
        with zipfile.ZipFile(archive_path) as zf:
            manifest = json.loads(zf.read(SOURCE_ARCHIVE_MANIFEST).decode("utf-8"))
            if manifest.get("schema") != SOURCE_ARCHIVE_SCHEMA:
                raise ValueError("지원하지 않는 Problem Studio 소스 아카이브입니다.")
            manifest_files = manifest.get("files") or []
            if not isinstance(manifest_files, list) or len(manifest_files) > SOURCE_ARCHIVE_MAX_FILES:
                raise ValueError("소스 아카이브 파일 수가 올바르지 않습니다.")
            if int(manifest.get("file_count", -1)) != len(manifest_files):
                raise ValueError("소스 아카이브 파일 수가 manifest와 다릅니다.")
            names = set(zf.namelist())
            seen_archive_names: set[str] = set()
            total_size = 0
            for index, entry in enumerate(manifest_files):
                if not isinstance(entry, dict):
                    raise ValueError("소스 아카이브 manifest가 올바르지 않습니다.")
                archive_name = str(entry.get("archive_name") or "")
                if archive_name not in names or not archive_name.startswith("files/"):
                    raise ValueError("소스 아카이브 파일 목록이 올바르지 않습니다.")
                if archive_name in seen_archive_names:
                    raise ValueError("소스 아카이브에 중복 파일이 있습니다.")
                seen_archive_names.add(archive_name)
                original_name = _safe_filename(str(entry.get("name") or f"source-{index + 1}"))
                target = root / f"{index:03d}_{original_name}"
                with zf.open(archive_name) as source, target.open("wb") as dest:
                    shutil.copyfileobj(source, dest, length=1024 * 1024)
                size = target.stat().st_size
                if size > TRANSFER_MAX_UPLOAD_BYTES:
                    raise ValueError(f"{original_name} 파일 크기가 너무 큽니다.")
                if size != int(entry.get("size", -1)):
                    raise ValueError(f"{original_name} 파일 크기가 manifest와 다릅니다.")
                total_size += size
                if total_size > SOURCE_ARCHIVE_MAX_TOTAL_BYTES:
                    raise ValueError("전체 업로드 크기가 너무 큽니다.")
                files.append(ArchivedSourceFile(
                    name=original_name,
                    path=target,
                    size=size,
                    content_type=str(entry.get("content_type") or "application/octet-stream"),
                ))
            if total_size != int(manifest.get("total_size", -1)):
                raise ValueError("소스 아카이브 전체 크기가 manifest와 다릅니다.")
        yield files
    finally:
        for item in files:
            item.close()
        shutil.rmtree(root, ignore_errors=True)
