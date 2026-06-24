from __future__ import annotations

import io
import zipfile
from datetime import datetime
from xml.sax.saxutils import escape

from apps.domains.tools.problem_studio.structure import normalize_space


HWPX_MIMETYPE = "application/hwp+zip"


def _xml(value: str) -> str:
    return escape(str(value or ""), {'"': "&quot;"})


def _paragraph_xml(text: str, paragraph_id: int, *, include_section: bool = False) -> str:
    section = ""
    if include_section:
        section = """
      <hp:run charPrIDRef="0">
        <hp:secPr id="0" textDirection="HORIZONTAL" spaceColumns="0" tabStop="8000" tabStopVal="0" outlineShapeIDRef="0" memoShapeIDRef="0">
          <hp:pagePr landscape="0" width="59528" height="84188" gutterType="LEFT_ONLY">
            <hp:margin header="4252" footer="4252" gutter="0" left="5668" right="5668" top="5668" bottom="5668"/>
          </hp:pagePr>
          <hp:grid lineGrid="0" charGrid="0" wonggojiFormat="0"/>
        </hp:secPr>
        <hp:ctrl>
          <hp:colPr id="0" type="NEWSPAPER" layout="LEFT" colCount="1" sameSz="1" sameGap="0"/>
        </hp:ctrl>
      </hp:run>"""
    body = f"<hp:t>{_xml(text)}</hp:t>" if text else "<hp:t/>"
    return f"""
    <hp:p id="{paragraph_id}" paraPrIDRef="0" styleIDRef="0" pageBreak="0" columnBreak="0" merged="0">{section}
      <hp:run charPrIDRef="0">{body}</hp:run>
      <hp:linesegarray>
        <hp:lineseg textpos="0" vertpos="0" vertsize="1500" textheight="1050" baseline="850" spacing="600" horzpos="0" horzsize="48190" flags="393216"/>
      </hp:linesegarray>
    </hp:p>"""


def _split_paragraphs(title: str, paragraphs: list[str]) -> list[str]:
    output = [title.strip()] if title.strip() else []
    for paragraph in paragraphs:
        normalized = normalize_space(paragraph)
        if not normalized:
            output.append("")
            continue
        output.extend(line.strip() for line in normalized.splitlines())
    return output or ["문제 검수본"]


def _content_hpf(title: str, creator: str, generated_at: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<opf:package xmlns:opf="http://www.idpf.org/2007/opf/" xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf" version="1.0" unique-identifier="uid" hpf:distribution="0">
  <opf:metadata>
    <opf:title>{_xml(title)}</opf:title>
    <opf:language>ko</opf:language>
    <opf:meta name="creator" content="text">{_xml(creator)}</opf:meta>
    <opf:meta name="CreatedDate" content="text">{_xml(generated_at)}</opf:meta>
    <opf:meta name="ModifiedDate" content="text">{_xml(generated_at)}</opf:meta>
  </opf:metadata>
  <opf:manifest>
    <opf:item id="header" href="Contents/header.xml" media-type="application/xml"/>
    <opf:item id="section0" href="Contents/section0.xml" media-type="application/xml"/>
  </opf:manifest>
  <opf:spine>
    <opf:itemref idref="section0"/>
  </opf:spine>
</opf:package>"""


def _container_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container" xmlns:hpf="http://www.hancom.co.kr/schema/2011/hpf">
  <ocf:rootfiles>
    <ocf:rootfile full-path="Contents/content.hpf" media-type="application/hwpml-package+xml"/>
    <ocf:rootfile full-path="Preview/PrvText.txt" media-type="text/plain"/>
  </ocf:rootfiles>
</ocf:container>"""


def _manifest_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<odf:manifest xmlns:odf="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">
  <odf:file-entry full-path="mimetype" media-type="application/hwp+zip"/>
  <odf:file-entry full-path="Contents/content.hpf" media-type="application/hwpml-package+xml"/>
  <odf:file-entry full-path="Contents/header.xml" media-type="application/xml"/>
  <odf:file-entry full-path="Contents/section0.xml" media-type="application/xml"/>
  <odf:file-entry full-path="Preview/PrvText.txt" media-type="text/plain"/>
  <odf:file-entry full-path="version.xml" media-type="application/xml"/>
  <odf:file-entry full-path="settings.xml" media-type="application/xml"/>
</odf:manifest>"""


def _version_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<hv:HCFVersion xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version" targetApplication="WORDPROCESSOR" major="5" minor="1" micro="0" buildNumber="1" os="1" xmlVersion="1.4" application="Academy Problem Studio" appVersion="1.0"/>"""


def _settings_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<ha:app xmlns:ha="http://www.hancom.co.kr/hwpml/2011/app">
  <ha:history/>
</ha:app>"""


def _header_xml() -> str:
    return """<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" xmlns:hc="http://www.hancom.co.kr/hwpml/2011/core" version="1.0">
  <hh:beginNum page="1" footnote="1" endnote="1" pic="1" tbl="1" equation="1"/>
  <hh:refList>
    <hh:fontfaces itemCnt="7">
      <hh:fontface lang="HANGUL" fontCnt="1"><hh:font id="0" face="맑은 고딕" type="TTF"/></hh:fontface>
      <hh:fontface lang="LATIN" fontCnt="1"><hh:font id="0" face="Arial" type="TTF"/></hh:fontface>
      <hh:fontface lang="HANJA" fontCnt="1"><hh:font id="0" face="맑은 고딕" type="TTF"/></hh:fontface>
      <hh:fontface lang="JAPANESE" fontCnt="1"><hh:font id="0" face="맑은 고딕" type="TTF"/></hh:fontface>
      <hh:fontface lang="OTHER" fontCnt="1"><hh:font id="0" face="Arial" type="TTF"/></hh:fontface>
      <hh:fontface lang="SYMBOL" fontCnt="1"><hh:font id="0" face="Arial" type="TTF"/></hh:fontface>
      <hh:fontface lang="USER" fontCnt="1"><hh:font id="0" face="Arial" type="TTF"/></hh:fontface>
    </hh:fontfaces>
    <hh:borderFills itemCnt="1">
      <hh:borderFill id="0" threeD="0" shadow="0" centerLine="NONE" breakCellSeparateLine="0">
        <hh:slash type="NONE" Crooked="0" isCounter="0"/>
        <hh:backSlash type="NONE" Crooked="0" isCounter="0"/>
        <hh:leftBorder type="NONE" width="0.1mm" color="#000000"/>
        <hh:rightBorder type="NONE" width="0.1mm" color="#000000"/>
        <hh:topBorder type="NONE" width="0.1mm" color="#000000"/>
        <hh:bottomBorder type="NONE" width="0.1mm" color="#000000"/>
        <hh:diagonal type="NONE" width="0.1mm" color="#000000"/>
      </hh:borderFill>
    </hh:borderFills>
    <hh:charProperties itemCnt="1">
      <hh:charPr id="0" height="1050" textColor="#000000" shadeColor="none" useFontSpace="0" useKerning="0">
        <hh:fontRef hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>
        <hh:ratio hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>
        <hh:spacing hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>
        <hh:relSz hangul="100" latin="100" hanja="100" japanese="100" other="100" symbol="100" user="100"/>
        <hh:offset hangul="0" latin="0" hanja="0" japanese="0" other="0" symbol="0" user="0"/>
      </hh:charPr>
    </hh:charProperties>
    <hh:tabProperties itemCnt="1"><hh:tabPr id="0" autoTabLeft="1" autoTabRight="1"/></hh:tabProperties>
    <hh:numberings itemCnt="0"/>
    <hh:bullets itemCnt="0"/>
    <hh:paraProperties itemCnt="1">
      <hh:paraPr id="0" tabPrIDRef="0" condense="0" fontLineHeight="0" snapToGrid="1" suppressLineNumbers="0" checked="0">
        <hh:align horizontal="JUSTIFY" vertical="BASELINE"/>
        <hh:heading type="NONE" idRef="0" level="0"/>
        <hh:breakSetting breakLatinWord="KEEP_WORD" breakNonLatinWord="0" widowOrphan="1" keepWithNext="0" keepLines="0" pageBreakBefore="0" lineWrap="BREAK"/>
        <hh:lineSpacing type="PERCENT" value="160" unit="HWPUNIT"/>
        <hh:border borderFillIDRef="0" offsetLeft="0" offsetRight="0" offsetTop="0" offsetBottom="0" connect="0" ignoreMargin="0"/>
        <hh:autoSpacing eAsianEng="0" eAsianNum="0"/>
        <hh:margin left="0" right="0" indent="0" prev="0" next="0"/>
      </hh:paraPr>
    </hh:paraProperties>
    <hh:styles itemCnt="1">
      <hh:style id="0" type="PARA" name="바탕글" engName="Normal" paraPrIDRef="0" charPrIDRef="0" nextStyleIDRef="0" langID="1042" lockForm="0"/>
    </hh:styles>
  </hh:refList>
</hh:head>"""


def _section_xml(paragraphs: list[str]) -> str:
    body = "\n".join(
        _paragraph_xml(text, 1000000001 + index, include_section=index == 0)
        for index, text in enumerate(paragraphs)
    )
    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<hs:sec xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section">
{body}
</hs:sec>"""


def build_hwpx_text_document(*, title: str, paragraphs: list[str], creator: str = "Academy Problem Studio") -> bytes:
    """Build a text-focused HWPX companion document.

    HWPX is an open ZIP/XML Hangul format. This writer intentionally sticks to
    plain paragraphs and embeds the same text in `Preview/PrvText.txt` so that
    text extraction and teacher review remain robust even when a strict editor
    ignores richer layout hints.
    """
    generated_at = datetime.now().replace(microsecond=0).isoformat()
    paragraph_list = _split_paragraphs(title, paragraphs)
    preview_text = normalize_space("\n".join(paragraph_list)) + "\n"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        info = zipfile.ZipInfo("mimetype")
        info.compress_type = zipfile.ZIP_STORED
        zf.writestr(info, HWPX_MIMETYPE)
        zf.writestr("META-INF/container.xml", _container_xml(), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("META-INF/manifest.xml", _manifest_xml(), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("version.xml", _version_xml(), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("settings.xml", _settings_xml(), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("Contents/content.hpf", _content_hpf(title, creator, generated_at), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("Contents/header.xml", _header_xml(), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("Contents/section0.xml", _section_xml(paragraph_list), compress_type=zipfile.ZIP_DEFLATED)
        zf.writestr("Preview/PrvText.txt", preview_text, compress_type=zipfile.ZIP_DEFLATED)
    return buffer.getvalue()
