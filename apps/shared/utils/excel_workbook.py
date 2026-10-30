"""Bounded OOXML archive validation shared by HTTP uploads and workers."""

import os
import zipfile
import zlib

from xml.etree.ElementTree import ParseError, XMLParser


MAX_XLSX_BYTES = 10 * 1024 * 1024
MAX_XLSX_MEMBERS = 2_000
MAX_XLSX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024


class _NoDoctype:
    # Validate XML incrementally without building a second in-memory worksheet tree.
    def start(self, tag, attrs):
        pass

    def end(self, tag):
        pass

    def data(self, data):
        pass

    def close(self):
        pass

    def doctype(self, name, pubid, system):
        raise ValueError("엑셀 XML에는 문서 형식 선언을 사용할 수 없습니다.")


def validate_xlsx_archive(source, *, max_uncompressed_bytes=MAX_XLSX_UNCOMPRESSED_BYTES) -> None:
    """Validate without copying the compressed upload; restore borrowed stream position."""
    borrowed = hasattr(source, "read")
    stream = source if borrowed else open(source, "rb")
    position = stream.tell()
    try:
        stream.seek(0, os.SEEK_END)
        if not 0 < stream.tell() <= MAX_XLSX_BYTES:
            raise ValueError("엑셀 파일은 비어 있지 않은 10MB 이하 파일이어야 합니다.")
        stream.seek(0)
        with zipfile.ZipFile(stream) as archive:
            members = archive.infolist()
            names = {member.filename for member in members}
            if (len(members) > MAX_XLSX_MEMBERS or len(names) != len(members)
                    or sum(member.file_size for member in members) > max_uncompressed_bytes
                    or any(member.flag_bits & 1 for member in members)):
                raise ValueError("엑셀 압축 파일의 크기 또는 구성이 허용 범위를 벗어났습니다.")
            if not {"[Content_Types].xml", "xl/workbook.xml"}.issubset(names):
                raise ValueError("올바른 .xlsx 파일이 아닙니다.")
            for member in members:
                parser = XMLParser(target=_NoDoctype()) if member.filename.lower().endswith((".xml", ".rels")) else None
                with archive.open(member) as entry:
                    while chunk := entry.read(64 * 1024):
                        if parser is not None:
                            parser.feed(chunk)
                # Reading to EOF validates CRC for every entry, including non-XML.
                if parser is not None:
                    parser.close()
    except (zipfile.BadZipFile, zipfile.LargeZipFile, KeyError, ParseError,
            RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        raise ValueError("올바른 .xlsx 파일이 아닙니다.") from exc
    finally:
        if borrowed:
            stream.seek(position)
        else:
            stream.close()
