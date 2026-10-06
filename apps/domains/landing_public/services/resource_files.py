"""Bounded format validation for public teaching documents; never trust MIME alone."""

import re
import zipfile
from xml.etree import ElementTree
from pathlib import PurePosixPath
from urllib.parse import quote

import olefile
from rest_framework.exceptions import ValidationError

from apps.infrastructure.storage.document_validation import validate_pdf_document

MAX_RESOURCE_BYTES = 30 * 1024 * 1024
RESOURCE_TYPES = {"pdf": "application/pdf", "hwp": "application/x-hwp", "hwpx": "application/hwp+zip"}


def validate_resource_file(upload):
    filename = str(upload.name or "").strip()
    if not filename or len(filename) > 200 or re.search(r"[\x00-\x1f\x7f/\\]", filename):
        raise ValidationError({"file": "파일 이름은 200자 이내이며 경로·제어 문자를 포함할 수 없습니다."})
    extension = filename.rsplit(".", 1)[-1].lower()[:200] if "." in filename else ""
    if upload.size <= 0 or upload.size > MAX_RESOURCE_BYTES:
        raise ValidationError({"file": "비어 있지 않은 30MB 이하 파일을 선택해주세요."})
    try:
        upload.seek(0)
        if extension == "pdf":
            head = upload.read(8)
            upload.seek(max(0, upload.size - 2048))
            if not re.fullmatch(rb"%PDF-(?:1\.[0-9]|2\.0)", head) or b"%%EOF" not in upload.read(2048):
                raise ValueError("invalid PDF")
            upload.seek(0)
            validate_pdf_document(upload.read(MAX_RESOURCE_BYTES + 1))
        elif extension == "hwp":
            with olefile.OleFileIO(upload) as document:
                header = document.openstream("FileHeader").read(256)
                if (
                    not header.startswith(b"HWP Document File\x00")
                    or len(header) < 40
                    or not document.exists("DocInfo")
                ):
                    raise ValueError("invalid HWP")
                if not any(path[0] in ("BodyText", "ViewText") for path in document.listdir()):
                    raise ValueError("missing HWP body")
        elif extension == "hwpx":
            with zipfile.ZipFile(upload) as document:
                entries = document.infolist()
                if not entries or len(entries) > 4096:
                    raise ValueError("archive entry limit")
                names = set()
                expanded = 0
                for entry in entries:
                    path = PurePosixPath(entry.filename)
                    if (
                        entry.filename in names
                        or path.is_absolute()
                        or ".." in path.parts
                        or "\\" in entry.filename
                        or re.search(r"[\x00-\x1f]", entry.filename)
                        or entry.flag_bits & 1
                    ):
                        raise ValueError("unsafe archive")
                    names.add(entry.filename)
                    expanded += entry.file_size
                    if entry.file_size > 64 * 1024 * 1024 or expanded > 128 * 1024 * 1024:
                        raise ValueError("archive expansion limit")
                    if entry.file_size > 1024 * 1024 and entry.file_size > max(entry.compress_size, 1) * 200:
                        raise ValueError("archive compression limit")
                mime = document.getinfo("mimetype")
                if mime.file_size > 100 or document.read(mime).strip() != b"application/hwp+zip":
                    raise ValueError("invalid HWPX MIME")
                if not {"Contents/content.hpf", "Contents/header.xml"}.issubset(names):
                    raise ValueError("missing HWPX document")
                sections = [name for name in names if re.fullmatch(r"Contents/section\d+\.xml", name)]
                if not sections:
                    raise ValueError("missing HWPX section")
                xml_roots = {
                    "Contents/content.hpf": "package",
                    "Contents/header.xml": "head",
                    **dict.fromkeys(sections, "sec"),
                }
                for entry in entries:
                    parser = ElementTree.XMLPullParser(events=("start", "end")) if entry.filename in xml_roots else None
                    root_name = None
                    tail = b""
                    with document.open(entry) as stream:
                        while block := stream.read(65536):
                            if parser is not None:
                                scan = (tail + block).upper()
                                if b"<!DOCTYPE" in scan or b"<!ENTITY" in scan:
                                    raise ValueError("unsafe XML")
                                tail = block[-16:]
                                parser.feed(block)
                                for event, element in parser.read_events():
                                    if event == "start" and root_name is None:
                                        root_name = element.tag.rsplit("}", 1)[-1]
                                    if event == "end":
                                        element.clear()
                    if parser is not None:
                        parser.close()
                        if root_name != xml_roots[entry.filename]:
                            raise ValueError("invalid HWPX XML root")
    except (ValueError, OSError, KeyError, EOFError, zipfile.BadZipFile, RuntimeError, ElementTree.ParseError) as error:
        raise ValidationError(
            {"file": "확장자와 실제 문서 형식이 다르거나 손상된 파일입니다. 원본 문서를 확인해주세요."}
        ) from error
    finally:
        upload.seek(0)
    return filename, extension, RESOURCE_TYPES.get(extension, "application/octet-stream")


def resource_content_disposition(filename):
    # Never interpolate an arbitrary Unicode/quoted suffix into an HTTP header.
    extension = filename.rsplit(".", 1)[-1].lower()[:200] if "." in filename else ""
    suffix = f".{extension}" if re.fullmatch(r"[a-z0-9]{1,10}", extension) else ""
    return f"attachment; filename=\"document{suffix}\"; filename*=UTF-8''{quote(filename, safe='')}"
