"""Bounded, credential-free document conversion subprocess for the public reader.

Only this Tools-worker entry point invokes the pinned native Hangul renderer.
Output contains plain text, table cells, formulas and re-encoded raster images;
document HTML, scripts, links and remote assets are never passed to the browser.
"""

from __future__ import annotations

import base64
import ctypes
import errno
import io
import json
import os
from pathlib import Path
import posixpath
import re
import subprocess
import sys
import zipfile
from urllib.parse import unquote
from xml.etree import ElementTree as ET

MAX_PAGES = 100
MAX_OUTPUT = 60 * 1024 * 1024
MAX_XML = 20 * 1024 * 1024
MAX_BLOCKS = 10000
MAX_IMAGE_PIXELS = 24_000_000


def _limits():
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_OUTPUT,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _deny_network():
    """Drop outbound IP sockets in this process and all native descendants."""
    class Comparison(ctypes.Structure):
        _fields_ = [("arg", ctypes.c_uint), ("op", ctypes.c_int),
                    ("a", ctypes.c_uint64), ("b", ctypes.c_uint64)]

    library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add_array.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
                                               ctypes.c_uint, ctypes.POINTER(Comparison)]
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    context = library.seccomp_init(0x7FFF0000)  # SCMP_ACT_ALLOW
    if not context:
        raise RuntimeError("Reader sandbox unavailable")
    try:
        denied = 0x00050000 | errno.EPERM
        syscall = library.seccomp_syscall_resolve_name(b"socket")
        for family in (2, 10):  # AF_INET, AF_INET6; LibreOffice's local pipes remain usable.
            comparison = Comparison(0, 4, family, 0)  # SCMP_CMP_EQ
            if library.seccomp_rule_add_array(context, denied, syscall, 1, ctypes.byref(comparison)):
                raise RuntimeError("Reader network sandbox unavailable")
        io_uring = library.seccomp_syscall_resolve_name(b"io_uring_setup")
        if io_uring >= 0 and library.seccomp_rule_add_array(context, denied, io_uring, 0, None):
            raise RuntimeError("Reader network sandbox unavailable")
        if library.seccomp_load(context):
            raise RuntimeError("Reader network sandbox unavailable")
    finally:
        library.seccomp_release(context)


def _validate_office_package(source, extension, *, budget=None, depth=0):
    """Office reports may contain embedded workbooks, but no active/linked inputs."""
    budget = budget if budget is not None else [0]
    if depth > 2:
        raise ValueError("Nested Office package limit")
    with zipfile.ZipFile(source) as package:
        entries = package.infolist()
        main = {"docx": "word/document.xml", "xlsx": "xl/workbook.xml", "pptx": "ppt/presentation.xml"}[extension]
        names = set(package.namelist())
        if len(entries) > 4096 or len(names) != len(entries) or not {main, "[Content_Types].xml", "_rels/.rels"}.issubset(names):
            raise ValueError("Invalid Office package")
        for entry in entries:
            name = entry.filename.lower()
            budget[0] += entry.file_size
            if (entry.flag_bits & 1 or name.startswith("/") or ".." in Path(name).parts
                    or "\\" in name or ":" in name or entry.file_size > 64 * 1024 * 1024
                    or budget[0] > 128 * 1024 * 1024
                    or (entry.file_size > 1024 * 1024 and entry.file_size > max(entry.compress_size, 1) * 200)):
                raise ValueError("Unsafe Office package")
            if ("vbaproject" in name or "/activex/" in name or "/externallinks/" in name
                    or ("/embeddings/" in name and not name.endswith(".xlsx"))):
                raise ValueError("Active or linked Office content")
            if name.endswith(".xlsx"):
                _validate_office_package(io.BytesIO(package.read(entry)), "xlsx", budget=budget, depth=depth + 1)
            if name.endswith(".svgz"):
                raise ValueError("Compressed linked image is not supported")
            if not name.endswith((".xml", ".rels", ".svg")):
                continue
            raw = package.read(entry)
            if len(raw) > 8 * 1024 * 1024:
                raise ValueError("Office XML size limit")
            text = raw.decode("utf-8-sig")
            if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
                raise ValueError("Unsafe Office XML")
            tree = ET.fromstring(text)
            if name.endswith(".svg") and (re.search(r"@import", text, re.I)
                    or any(not value.strip().strip("'\"").startswith(("#", "data:image/png;", "data:image/jpeg;", "data:image/webp;", "data:image/gif;"))
                           for value in re.findall(r"url\(([^)]*)\)", text, re.I))
                    or any(node.tag.rsplit("}", 1)[-1].lower() == "foreignobject" for node in tree.iter())):
                raise ValueError("Linked Office image style")
            instructions = "".join((node.text or "") for node in tree.iter()
                                   if node.tag.rsplit("}", 1)[-1] == "instrText")
            if re.search(r"INCLUDETEXT|INCLUDEPICTURE|DDEAUTO|\bDDE\b", instructions, re.I):
                raise ValueError("Linked Office instruction")
            for node in tree.iter():
                tag = node.tag.rsplit("}", 1)[-1]
                if name.endswith(".svg") and (tag == "script" or any(
                    key.rsplit("}", 1)[-1] == "href" and not value.startswith(("#", "data:image/png;", "data:image/jpeg;", "data:image/webp;", "data:image/gif;"))
                    for key, value in node.attrib.items()
                )):
                    raise ValueError("Linked Office image")
                if tag == "Relationship":
                    if node.get("TargetMode", "").lower() == "external":
                        if not node.get("Type", "").endswith("/hyperlink"):
                            raise ValueError("Linked Office resource")
                    else:
                        target = unquote(node.get("Target", ""))
                        base = posixpath.dirname(posixpath.dirname(name))
                        resolved = posixpath.normpath(posixpath.join(base, target))
                        if ":" in target or "\\" in target or (target.startswith("/") and target[1:] not in names) or resolved == ".." or resolved.startswith("../"):
                            raise ValueError("Office resource outside package")
                if tag in ("instrText", "fldSimple", "f"):
                    field = " ".join([node.text or "", *node.attrib.values()])
                    if re.search(r"\b(?:INCLUDETEXT|INCLUDEPICTURE|DDE|DDEAUTO|WEBSERVICE|FILTERXML|IMPORTXML|IMAGE)\b", field, re.I):
                        raise ValueError("Linked Office field")


def _office_pdf(source, output, extension):
    _validate_office_package(source, extension)
    profile = output / "office-profile" / "user"
    profile.mkdir(parents=True)
    (profile / "registrymodifications.xcu").write_text(
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry">'
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="MacroSecurityLevel" oor:op="fuse"><value>3</value></prop>'
        '<prop oor:name="DisableMacrosExecution" oor:op="fuse"><value>true</value></prop>'
        '</item></oor:items>'
    )
    _native("/usr/local/bin/resource-office", "--headless", "--nologo", "--nodefault", "--norestore", "--unaccept=all",
            f"-env:UserInstallation={profile.parent.as_uri()}", "--convert-to", "pdf", "--outdir", output, source)
    generated = output / f"{source.stem}.pdf"
    if not generated.is_file():
        raise ValueError("Office document could not be rendered")
    generated.rename(output / "pages.pdf")


def _native(binary, *args):
    # Parent already removes credentials and gives this process a private HOME.
    subprocess.run([binary, *map(str, args)], check=True, timeout=75,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def _image(raw, output, assets):
    from PIL import Image, ImageOps

    if len(raw) > 20 * 1024 * 1024 or len(assets) >= 100:
        raise ValueError("Image limit")
    with Image.open(io.BytesIO(raw)) as source:
        if source.width * source.height > MAX_IMAGE_PIXELS:
            raise ValueError("Image dimensions")
        frame_count = getattr(source, "n_frames", 1)
        if frame_count > 1:
            if frame_count > 200 or source.width * source.height * frame_count > 48_000_000 or source.format not in ("GIF", "PNG", "WEBP"):
                raise ValueError("Animated image limit")
            for frame in range(frame_count):
                source.seek(frame)
                source.load()
            name = f"image-{len(assets)}.{source.format.lower()}"
            (output / name).write_bytes(raw)
            assets.append(name)
            return {"kind": "image", "asset": name, "width": source.width, "height": source.height}
        oriented = ImageOps.exif_transpose(source)
        oriented.thumbnail((2400, 2400))
        image = oriented.convert("RGBA" if "A" in oriented.getbands() or "transparency" in oriented.info else "RGB")
        name = f"image-{len(assets)}.webp"
        image.save(output / name, "WEBP", quality=88)
        assets.append(name)
        return {"kind": "image", "asset": name, "width": image.width, "height": image.height}


def _doclang(path, output, assets):
    raw = path.read_bytes()
    if len(raw) > MAX_XML or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("Document structure limit")
    root = ET.fromstring(raw)
    if root.tag != "doclang" or root.get("version") != "0.6":
        raise ValueError("Document structure version")
    count = 0
    complete = True

    def blocks(nodes, depth=0):
        nonlocal count, complete
        if depth > 10:
            raise ValueError("Document nesting limit")
        result = []
        for node in nodes:
            count += 1
            if count > MAX_BLOCKS:
                raise ValueError("Document size limit")
            text = "".join(node.itertext())
            if node.tag == "text":
                if text.strip():
                    result.append({"kind": "paragraph", "text": text})
            elif node.tag == "formula":
                result.append({"kind": "formula", "text": text})
            elif node.tag == "picture":
                src = node.find("src")
                uri = src.get("uri", "") if src is not None else ""
                if not uri.startswith("data:image/") or ";base64," not in uri:
                    complete = False
                    continue
                try:
                    result.append(_image(base64.b64decode(uri.split(";base64,", 1)[1], validate=True), output, assets))
                    if text.strip():
                        result.append({"kind": "paragraph", "text": text})
                except (ValueError, OSError):
                    complete = False
            elif node.tag == "table":
                rows, row, cell = [], [], None
                for item in node:
                    if item.tag == "fcel":
                        if cell is not None:
                            row.append(blocks(cell, depth + 1))
                        cell = []
                        # Complicated merged grids use the preserved page view.
                        if item.attrib:
                            complete = False
                    elif item.tag == "nl":
                        if cell is not None:
                            row.append(blocks(cell, depth + 1))
                        cell = None
                        if row:
                            rows.append(row); row = []
                    else:
                        if item.tag in ("ecel", "mcel", "cel"):
                            complete = False
                        if cell is None:
                            cell = []
                        cell.append(item)
                if cell is not None:
                    row.append(blocks(cell, depth + 1))
                if row:
                    rows.append(row)
                if rows:
                    result.append({"kind": "table", "rows": rows})
            elif node.tag in ("section", "body", "list", "item", "quote"):
                result.extend(blocks(node, depth + 1))
            else:
                # Never call partial extraction a complete report.
                complete = False
        return result

    body = blocks(root)
    return body, complete and bool(body)


def render(source: Path, output: Path, extension: str, binary="/usr/local/bin/rhwp", font_path="/usr/share/fonts/truetype/nanum"):
    import fitz

    output.mkdir(exist_ok=True)
    assets = []
    data = {"version": 1, "blocks": [], "assets": assets, "mode": "article"}
    if extension in ("hwp", "hwpx"):
        pdf = output / "pages.pdf"
        _native(binary, "export-pdf", source, "-o", pdf, "--font-path", font_path,
                "--fallback-serif", "NanumGothic", "--fallback-sans", "NanumGothic",
                "--fallback-mono", "NanumGothic")
        xml = output / "body.xml"
        _native(binary, "export-doclang", source, "-o", xml)
        data["blocks"], complete = _doclang(xml, output, assets)
        with fitz.open(pdf) as document:
            if not 0 < len(document) <= MAX_PAGES:
                raise ValueError("Page limit")
            data["pages"] = len(document)
            # Missing Hangul fonts previously produced blank successful PDFs.
            if any(b.get("text") for b in data["blocks"]) and not any(page.get_text().strip() for page in document):
                raise ValueError("Blank rendered document")
        assets.append("pages.pdf")
        data["pdf"] = "pages.pdf"
        if not complete:
            data["mode"] = "pages"
            data["blocks"] = []
    elif extension in ("docx", "xlsx", "pptx"):
        _office_pdf(source, output, extension)
        with fitz.open(output / "pages.pdf") as document:
            if not 0 < len(document) <= MAX_PAGES:
                raise ValueError("Page limit")
            data["pages"] = len(document)
        assets.append("pages.pdf")
        data.update({"pdf": "pages.pdf", "mode": "pages"})
    elif extension in ("png", "jpg", "jpeg", "webp", "gif"):
        data["blocks"] = [_image(source.read_bytes(), output, assets)]
    elif extension in ("txt", "md"):
        raw = source.read_bytes()
        if len(raw) > 1024 * 1024:
            raise ValueError("Text limit")
        text = raw.decode("utf-8-sig")
        if not text.strip() or "\x00" in text:
            raise ValueError("Unreadable text")
        data["blocks"] = [{"kind": "paragraph", "text": text}]
    else:
        raise ValueError("Unsupported reader format")
    serialized = json.dumps(data, ensure_ascii=False).encode()
    if len(serialized) > 2 * 1024 * 1024 or sum((output / name).stat().st_size for name in assets) > MAX_OUTPUT:
        raise ValueError("Reader output limit")
    (output / "manifest.json").write_bytes(serialized)
    return data


if __name__ == "__main__":
    _limits()
    _deny_network()
    render(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3],
           os.environ.get("RESOURCE_RHWP_BINARY", "/usr/local/bin/rhwp"),
           os.environ.get("RESOURCE_READER_FONT_PATH", "/usr/share/fonts/truetype/nanum"))
