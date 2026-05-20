from __future__ import annotations

from collections.abc import Iterable
import zipfile

from rest_framework.exceptions import ValidationError


EXCEL_EXTENSIONS = {"xlsx", "xls", "csv"}
EXCEL_CONTENT_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "text/csv",
    "application/csv",
}
OMR_EXTENSIONS = {"jpg", "jpeg", "png", "tif", "tiff", "pdf"}
OMR_CONTENT_TYPES = {"image/jpeg", "image/png", "image/tiff", "application/pdf"}
DEFAULT_MAX_EXCEL_SIZE = 10 * 1024 * 1024
DEFAULT_MAX_OMR_SIZE = 10 * 1024 * 1024


def _extension(upload_file) -> str:
    name = str(getattr(upload_file, "name", "") or "")
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def _read_prefix(upload_file, size: int = 8) -> bytes:
    pos = upload_file.tell() if hasattr(upload_file, "tell") else 0
    try:
        upload_file.seek(0)
        return upload_file.read(size) or b""
    finally:
        try:
            upload_file.seek(pos)
        except Exception:
            upload_file.seek(0)


def _read_bytes(upload_file) -> bytes:
    pos = upload_file.tell() if hasattr(upload_file, "tell") else 0
    try:
        upload_file.seek(0)
        return upload_file.read() or b""
    finally:
        try:
            upload_file.seek(pos)
        except Exception:
            upload_file.seek(0)


def _validate_excel_content(upload_file, ext: str, label: str) -> None:
    prefix = _read_prefix(upload_file, size=16)
    if ext == "xlsx":
        if not prefix.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")):
            raise ValidationError({"detail": f"{label} 파일 내용을 읽을 수 없습니다."})
        data = _read_bytes(upload_file)
        try:
            from io import BytesIO

            with zipfile.ZipFile(BytesIO(data)) as archive:
                names = set(archive.namelist())
                has_content_types = "[Content_Types].xml" in names
                has_workbook = any(name.startswith("xl/") for name in names)
                if not (has_content_types and has_workbook):
                    raise ValidationError({"detail": f"{label} 파일 내용을 읽을 수 없습니다."})
        except zipfile.BadZipFile:
            raise ValidationError({"detail": f"{label} 파일 내용을 읽을 수 없습니다."})
        return

    if ext == "xls":
        if not prefix.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise ValidationError({"detail": f"{label} 파일 내용을 읽을 수 없습니다."})
        return

    if ext == "csv":
        sample = _read_bytes(upload_file)[:4096]
        if b"\x00" in sample:
            raise ValidationError({"detail": f"{label} 파일 내용을 읽을 수 없습니다."})
        for encoding in ("utf-8-sig", "cp949"):
            try:
                sample.decode(encoding)
                return
            except UnicodeDecodeError:
                continue
        raise ValidationError({"detail": f"{label} 파일 내용을 읽을 수 없습니다."})


def _pdf_page_count(upload_file) -> int:
    pos = upload_file.tell() if hasattr(upload_file, "tell") else 0
    try:
        upload_file.seek(0)
        data = upload_file.read()
        import fitz  # PyMuPDF

        with fitz.open(stream=data, filetype="pdf") as doc:
            return len(doc)
    finally:
        try:
            upload_file.seek(pos)
        except Exception:
            upload_file.seek(0)


def validate_uploaded_file(
    upload_file,
    *,
    allowed_extensions: Iterable[str],
    allowed_content_types: Iterable[str],
    max_size: int,
    label: str = "file",
    pdf_single_page: bool = False,
) -> None:
    ext = _extension(upload_file)
    allowed_exts = {e.lower() for e in allowed_extensions}
    if ext not in allowed_exts:
        raise ValidationError(
            {"detail": f"{label} 형식이 허용되지 않습니다. (허용 확장자: {', '.join(sorted(allowed_exts))})"}
        )

    size = int(getattr(upload_file, "size", 0) or 0)
    if size <= 0:
        raise ValidationError({"detail": f"{label}이 비어 있습니다."})
    if size > max_size:
        mb = max_size // (1024 * 1024)
        raise ValidationError({"detail": f"{label} 크기가 {mb}MB를 초과합니다."})

    content_type = (getattr(upload_file, "content_type", "") or "").lower()
    allowed_types = {t.lower() for t in allowed_content_types}
    if content_type and content_type != "application/octet-stream" and content_type not in allowed_types:
        raise ValidationError({"detail": f"{label} MIME 형식이 허용되지 않습니다."})

    if ext in EXCEL_EXTENSIONS:
        _validate_excel_content(upload_file, ext, label)

    if ext in OMR_EXTENSIONS:
        prefix = _read_prefix(upload_file)
        is_pdf = ext == "pdf"
        looks_valid = (
            (ext in {"jpg", "jpeg"} and prefix.startswith(b"\xff\xd8"))
            or (ext == "png" and prefix.startswith(b"\x89PNG"))
            or (ext in {"tif", "tiff"} and (prefix.startswith(b"II*\x00") or prefix.startswith(b"MM\x00*")))
            or (is_pdf and prefix.startswith(b"%PDF"))
        )
        if not looks_valid:
            raise ValidationError({"detail": f"{label} 파일 내용을 읽을 수 없습니다."})
        if is_pdf and pdf_single_page:
            try:
                page_count = _pdf_page_count(upload_file)
            except Exception:
                raise ValidationError({"detail": f"{label} PDF를 읽을 수 없습니다."})
            if page_count != 1:
                raise ValidationError(
                    {
                        "detail": (
                            f"{label}은 {page_count}페이지 PDF입니다. "
                            "OMR은 답안지 1장당 1개 파일로 업로드해 주세요."
                        )
                    }
                )
