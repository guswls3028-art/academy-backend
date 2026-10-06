"""Conversion failure boundaries use synthetic documents only."""
import io
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import TestCase, skipUnless
import zipfile

from PIL import Image
from apps.infrastructure.storage.resource_document_renderer import _image, _validate_office_package


class ResourceDocumentRendererTests(TestCase):
    def office(self, additions=None):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as package:
            for name, value in {
                '[Content_Types].xml': '<Types/>', '_rels/.rels': '<Relationships/>',
                'word/document.xml': '<document><p>QA 공개 분석 보고서</p></document>',
                **(additions or {}),
            }.items():
                package.writestr(name, value)
        buffer.seek(0)
        return buffer

    def test_plain_office_document_and_nonfetched_hyperlink_are_readable(self):
        _validate_office_package(self.office({'word/_rels/document.xml.rels':
            '<Relationships><Relationship Type="https://schemas.example/hyperlink" '
            'TargetMode="External" Target="https://example.com"/></Relationships>'}), 'docx')

    def test_linked_or_active_office_inputs_are_rejected(self):
        cases = [
            {'word/_rels/document.xml.rels': '<Relationships><Relationship Type="image" TargetMode="External" Target="file:///etc/passwd"/></Relationships>'},
            {'word/document.xml': '<document><instrText>INCLUDE</instrText><instrText>TEXT /etc/passwd</instrText></document>'},
            {'word/vbaProject.bin': 'macro'},
            {'word/embeddings/linked.bin': 'OLE'},
            {'word/media/picture.svg': '<svg><image href="file:///etc/passwd"/></svg>'},
            {'word/media/picture.svg': '<svg><style>path { fill: url(file:///etc/passwd); }</style></svg>'},
            {'word/_rels/document.xml.rels': '<Relationships><Relationship Type="image" Target="file:///etc/passwd"/></Relationships>'},
            {'word/_rels/document.xml.rels': '<Relationships><Relationship Type="image" Target="../../etc/passwd"/></Relationships>'},
            {'word/document.xml': '<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'},
            {'../another-tenant.xml': '<x/>'},
        ]
        for case in cases:
            with self.subTest(case=list(case)), self.assertRaises(ValueError):
                _validate_office_package(self.office(case), 'docx')

    def test_exif_orientation_and_transparent_image_survive_conversion(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp); assets = []
            image = Image.new('RGB', (30, 10), 'red')
            exif = image.getexif(); exif[274] = 6
            buffer = io.BytesIO(); image.save(buffer, 'JPEG', exif=exif)
            block = _image(buffer.getvalue(), output, assets)
            self.assertEqual((block['width'], block['height']), (10, 30))
            transparent = Image.new('RGBA', (4, 4), (10, 20, 30, 0))
            buffer = io.BytesIO(); transparent.save(buffer, 'PNG')
            block = _image(buffer.getvalue(), output, assets)
            with Image.open(output / block['asset']) as actual:
                self.assertEqual(actual.getpixel((0, 0))[3], 0)

    def test_animation_preserves_frames_and_rejects_excessive_frame_budget(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp); assets = []
            first = Image.new('RGB', (8, 8), 'red'); second = Image.new('RGB', (8, 8), 'blue')
            buffer = io.BytesIO(); first.save(buffer, 'GIF', save_all=True, append_images=[second], duration=100, loop=0)
            block = _image(buffer.getvalue(), output, assets)
            with Image.open(output / block['asset']) as actual:
                self.assertEqual(actual.n_frames, 2)
            from unittest.mock import patch
            with patch('apps.infrastructure.storage.resource_document_renderer.MAX_IMAGE_PIXELS', 10):
                with self.assertRaises(ValueError):
                    _image(buffer.getvalue(), output, assets)

    @skipUnless(sys.platform == 'linux', 'Production reader sandbox uses Linux seccomp')
    def test_network_is_denied_in_native_descendants(self):
        # Never load the sandbox into the test runner itself.
        program = '''
from apps.infrastructure.storage.resource_document_renderer import _deny_network
import socket, subprocess, sys
_deny_network()
for family in (socket.AF_INET, socket.AF_INET6):
    try: socket.socket(family)
    except PermissionError: pass
    else: raise AssertionError('Network allowed')
socket.socket(socket.AF_UNIX).close()
child = subprocess.run([sys.executable, '-c', 'import socket; socket.socket()'], capture_output=True)
assert child.returncode != 0 and b'PermissionError' in child.stderr
'''
        subprocess.run([sys.executable, '-c', program], check=True, timeout=15)
