from io import BytesIO
import zipfile

import pytest
from openpyxl import Workbook

from apps.shared.utils.excel_workbook import validate_xlsx_archive


def test_archive_validation_borrows_stream_without_copying_or_closing():
    class Borrowed(BytesIO):
        def read(self, size=-1):
            assert size != -1 or self.tell() != 0, "do not copy the entire upload"
            return super().read(size)

    stream = BytesIO()
    Workbook().save(stream)
    borrowed = Borrowed(stream.getvalue())
    borrowed.seek(7)
    validate_xlsx_archive(borrowed)
    assert borrowed.tell() == 7
    assert not borrowed.closed


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_xml_entity_declaration_is_rejected(encoding):
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", '<!DOCTYPE x [<!ENTITY x "unsafe">]><x>&x;</x>'.encode(encoding))
        archive.writestr("xl/workbook.xml", "<workbook/>")
    stream.seek(3)
    with pytest.raises(ValueError):
        validate_xlsx_archive(stream)
    assert stream.tell() == 3


def test_duplicate_archive_members_and_expansion_are_rejected():
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    with pytest.raises(ValueError, match="허용 범위"):
        validate_xlsx_archive(stream, max_uncompressed_bytes=1)
    with zipfile.ZipFile(stream, "a") as archive:
        with pytest.warns(UserWarning):
            archive.writestr("xl/workbook.xml", "<workbook/>")
    with pytest.raises(ValueError, match="허용 범위"):
        validate_xlsx_archive(stream)
