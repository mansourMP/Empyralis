from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from typing import Any, Dict, List
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
import zipfile

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CP_NS = "http://schemas.openxmlformats.org/package/2006/metadata/core-properties"
DC_NS = "http://purl.org/dc/elements/1.1/"
DCTERMS_NS = "http://purl.org/dc/terms/"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"
VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"

for prefix, uri in {
    'w': W_NS,
    'a': A_NS,
    'p': P_NS,
    'r': R_NS,
    'cp': CP_NS,
    'dc': DC_NS,
    'dcterms': DCTERMS_NS,
    'xsi': XSI_NS,
    'vt': VT_NS,
}.items():
    ET.register_namespace(prefix, uri)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


@dataclass
class DocSection:
    heading: str
    body: List[str]


@dataclass
class DeckSlide:
    title: str
    bullets: List[str]


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def _text_lines(value: Any) -> List[str]:
    if isinstance(value, list):
        return [text for item in value if (text := str(item or '').strip())]
    text = str(value or '').strip()
    if not text:
        return []
    return [line.strip() for line in text.replace('\r', '\n').split('\n') if line.strip()]


def normalize_doc_sections(payload: Any, *, fallback_title: str) -> Dict[str, Any]:
    title = fallback_title
    sections: List[DocSection] = []
    if isinstance(payload, dict):
        if str(payload.get('title') or '').strip():
            title = str(payload.get('title')).strip()
        raw_sections = payload.get('sections')
        if isinstance(raw_sections, list):
            for idx, item in enumerate(raw_sections):
                if not isinstance(item, dict):
                    continue
                heading = str(item.get('heading') or item.get('title') or f'Section {idx + 1}').strip() or f'Section {idx + 1}'
                body = _text_lines(item.get('body') or item.get('content') or item.get('bullets'))
                sections.append(DocSection(heading=heading, body=body or ['']))
    if not sections:
        raw = str(payload or '').strip() if not isinstance(payload, dict) else ''
        chunks = [chunk.strip() for chunk in raw.split('\n\n') if chunk.strip()]
        for idx, chunk in enumerate(chunks):
            lines = _text_lines(chunk)
            if not lines:
                continue
            heading = lines[0] if idx > 0 or len(lines) > 1 else 'Summary'
            body = lines[1:] if len(lines) > 1 else lines
            sections.append(DocSection(heading=heading, body=body or ['']))
    if not sections:
        sections = [DocSection(heading='Summary', body=['Add your content here.'])]
    return {'title': title, 'sections': sections}


def normalize_deck_slides(payload: Any, *, fallback_title: str) -> Dict[str, Any]:
    title = fallback_title
    slides: List[DeckSlide] = []
    if isinstance(payload, dict):
        if str(payload.get('title') or '').strip():
            title = str(payload.get('title')).strip()
        raw_slides = payload.get('slides')
        if isinstance(raw_slides, list):
            for idx, item in enumerate(raw_slides):
                if not isinstance(item, dict):
                    continue
                slide_title = str(item.get('title') or item.get('heading') or f'Slide {idx + 1}').strip() or f'Slide {idx + 1}'
                bullets = _text_lines(item.get('bullets') or item.get('body') or item.get('content'))
                slides.append(DeckSlide(title=slide_title, bullets=bullets or ['']))
    if not slides:
        raw = str(payload or '').strip() if not isinstance(payload, dict) else ''
        chunks = [chunk.strip() for chunk in raw.split('\n\n') if chunk.strip()]
        for idx, chunk in enumerate(chunks):
            lines = _text_lines(chunk)
            if not lines:
                continue
            slide_title = lines[0] if len(lines) > 1 else f'Slide {idx + 1}'
            bullets = lines[1:] if len(lines) > 1 else lines
            slides.append(DeckSlide(title=slide_title, bullets=bullets or ['']))
    if not slides:
        slides = [DeckSlide(title=title, bullets=['Add your first point here.'])]
    return {'title': title, 'slides': slides}


def extract_docx_paragraphs(blob: bytes) -> List[str]:
    if not blob:
        return []
    with zipfile.ZipFile(BytesIO(blob), 'r') as archive:
        try:
            raw = archive.read('word/document.xml')
        except KeyError:
            return []
    root = ET.fromstring(raw)
    paragraphs: List[str] = []
    for paragraph in root.findall(f'.//{{{W_NS}}}p'):
        texts = [node.text or '' for node in paragraph.findall(f'.//{{{W_NS}}}t') if (node.text or '').strip()]
        joined = ''.join(texts).strip()
        if joined:
            paragraphs.append(joined)
    return paragraphs


def extract_pptx_slides(blob: bytes) -> List[DeckSlide]:
    if not blob:
        return []
    slides: List[DeckSlide] = []
    with zipfile.ZipFile(BytesIO(blob), 'r') as archive:
        slide_names = sorted(name for name in archive.namelist() if name.startswith('ppt/slides/slide') and name.endswith('.xml'))
        for name in slide_names:
            raw = archive.read(name)
            root = ET.fromstring(raw)
            texts = [node.text or '' for node in root.findall(f'.//{{{A_NS}}}t') if (node.text or '').strip()]
            if texts:
                slides.append(DeckSlide(title=texts[0], bullets=texts[1:] or ['']))
    return slides


def _docx_document_xml(paragraphs: List[str]) -> bytes:
    document = ET.Element(f'{{{W_NS}}}document')
    body = ET.SubElement(document, f'{{{W_NS}}}body')
    for text in paragraphs:
        paragraph = ET.SubElement(body, f'{{{W_NS}}}p')
        run = ET.SubElement(paragraph, f'{{{W_NS}}}r')
        node = ET.SubElement(run, f'{{{W_NS}}}t')
        if text.startswith(' ') or text.endswith(' '):
            node.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
        node.text = text
    sect = ET.SubElement(body, f'{{{W_NS}}}sectPr')
    pg_sz = ET.SubElement(sect, f'{{{W_NS}}}pgSz')
    pg_sz.set(f'{{{W_NS}}}w', '12240')
    pg_sz.set(f'{{{W_NS}}}h', '15840')
    pg_mar = ET.SubElement(sect, f'{{{W_NS}}}pgMar')
    for key, value in {'top': '1440', 'right': '1440', 'bottom': '1440', 'left': '1440', 'header': '720', 'footer': '720', 'gutter': '0'}.items():
        pg_mar.set(f'{{{W_NS}}}{key}', value)
    return ET.tostring(document, encoding='utf-8', xml_declaration=True)


def _write_common_docx_parts(archive: zipfile.ZipFile, title: str, paragraphs: List[str]) -> None:
    archive.writestr('[Content_Types].xml', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>''')
    archive.writestr('_rels/.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>''')
    archive.writestr('docProps/core.xml', f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="{CP_NS}" xmlns:dc="{DC_NS}" xmlns:dcterms="{DCTERMS_NS}" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="{XSI_NS}">
  <dc:title>{escape(title)}</dc:title>
  <dc:creator>Empyralis</dc:creator>
  <cp:lastModifiedBy>Empyralis</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{_iso_now()}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{_iso_now()}</dcterms:modified>
</cp:coreProperties>''')
    archive.writestr('docProps/app.xml', f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="{VT_NS}">
  <Application>Empyralis</Application>
</Properties>''')
    archive.writestr('word/document.xml', _docx_document_xml(paragraphs))


def build_docx(title: str, sections: List[DocSection]) -> bytes:
    paragraphs: List[str] = [title]
    for section in sections:
        if section.heading:
            paragraphs.append(section.heading)
        paragraphs.extend([line for line in section.body if str(line or '').strip()])
    mem = BytesIO()
    with zipfile.ZipFile(mem, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        _write_common_docx_parts(archive, title, paragraphs)
    return mem.getvalue()


def build_updated_docx(existing_blob: bytes | None, title: str, sections: List[DocSection]) -> bytes:
    paragraphs = extract_docx_paragraphs(existing_blob or b'')
    if not paragraphs:
        return build_docx(title, sections)
    paragraphs.append('')
    for section in sections:
        if section.heading:
            paragraphs.append(section.heading)
        paragraphs.extend([line for line in section.body if str(line or '').strip()])
    mem = BytesIO()
    with zipfile.ZipFile(mem, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        _write_common_docx_parts(archive, title, paragraphs)
    return mem.getvalue()


def _ppt_slide_xml(slide: DeckSlide) -> str:
    title = escape(slide.title or 'Slide')
    bullets = []
    for bullet in slide.bullets or ['']:
        clean = escape(str(bullet or '').strip())
        if clean:
            bullets.append(f'<a:p><a:pPr lvl="0" marL="342900" indent="-285750"><a:buChar char="•"/></a:pPr><a:r><a:rPr lang="en-US" sz="1800"/><a:t>{clean}</a:t></a:r><a:endParaRPr lang="en-US" sz="1800"/></a:p>')
    if not bullets:
        bullets.append('<a:p><a:endParaRPr lang="en-US" sz="1800"/></a:p>')
    body_xml = ''.join(bullets)
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">
  <p:cSld>
    <p:spTree>
      <p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>
      <p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="2" name="Title 1"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
        <p:spPr><a:xfrm><a:off x="457200" y="274638"/><a:ext cx="8229600" cy="1143000"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
        <p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:r><a:rPr lang="en-US" sz="2800" b="1"/><a:t>{title}</a:t></a:r><a:endParaRPr lang="en-US" sz="2800"/></a:p></p:txBody>
      </p:sp>
      <p:sp>
        <p:nvSpPr><p:cNvPr id="3" name="Content Placeholder 2"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>
        <p:spPr><a:xfrm><a:off x="685800" y="1600200"/><a:ext cx="7772400" cy="4114800"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
        <p:txBody><a:bodyPr wrap="square"/><a:lstStyle/>{body_xml}</p:txBody>
      </p:sp>
    </p:spTree>
  </p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>'''


def build_pptx(title: str, slides: List[DeckSlide]) -> bytes:
    if not slides:
        slides = [DeckSlide(title=title, bullets=['Add your first point here.'])]
    mem = BytesIO()
    with zipfile.ZipFile(mem, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        slide_overrides: List[str] = []
        slide_rels: List[str] = []
        slide_ids: List[str] = []
        for idx, slide in enumerate(slides, start=1):
            archive.writestr(f'ppt/slides/slide{idx}.xml', _ppt_slide_xml(slide))
            archive.writestr(f'ppt/slides/_rels/slide{idx}.xml.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>''')
            slide_overrides.append(f'<Override PartName="/ppt/slides/slide{idx}.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>')
            slide_rels.append(f'<Relationship Id="rId{idx + 4}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{idx}.xml"/>')
            slide_ids.append(f'<p:sldId id="{256 + idx}" r:id="rId{idx + 4}"/>')
        archive.writestr('[Content_Types].xml', f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>
  <Override PartName="/ppt/presProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presProps+xml"/>
  <Override PartName="/ppt/viewProps.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml"/>
  <Override PartName="/ppt/tableStyles.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml"/>
  <Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>
  <Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>
  <Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>
  <Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
  <Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
  {''.join(slide_overrides)}
</Types>''')
        archive.writestr('_rels/.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="ppt/presentation.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>''')
        archive.writestr('docProps/core.xml', f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="{CP_NS}" xmlns:dc="{DC_NS}" xmlns:dcterms="{DCTERMS_NS}" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="{XSI_NS}">
  <dc:title>{escape(title)}</dc:title>
  <dc:creator>Empyralis</dc:creator>
  <cp:lastModifiedBy>Empyralis</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{_iso_now()}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{_iso_now()}</dcterms:modified>
</cp:coreProperties>''')
        archive.writestr('docProps/app.xml', f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="{VT_NS}">
  <Application>Empyralis</Application>
  <Slides>{len(slides)}</Slides>
  <Notes>0</Notes>
  <HiddenSlides>0</HiddenSlides>
  <MMClips>0</MMClips>
  <ScaleCrop>false</ScaleCrop>
  <HeadingPairs><vt:vector size="2" baseType="variant"><vt:variant><vt:lpstr>Slides</vt:lpstr></vt:variant><vt:variant><vt:i4>{len(slides)}</vt:i4></vt:variant></vt:vector></HeadingPairs>
  <TitlesOfParts><vt:vector size="{len(slides)}" baseType="lpstr">{''.join(f'<vt:lpstr>{escape(slide.title)}</vt:lpstr>' for slide in slides)}</vt:vector></TitlesOfParts>
</Properties>''')
        archive.writestr('ppt/presentation.xml', f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentation xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">
  <p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>
  <p:sldIdLst>{''.join(slide_ids)}</p:sldIdLst>
  <p:sldSz cx="9144000" cy="6858000"/>
  <p:notesSz cx="6858000" cy="9144000"/>
</p:presentation>''')
        archive.writestr('ppt/_rels/presentation.xml.rels', f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="slideMasters/slideMaster1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/presProps" Target="presProps.xml"/>
  <Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/viewProps" Target="viewProps.xml"/>
  <Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/tableStyles" Target="tableStyles.xml"/>
  {''.join(slide_rels)}
</Relationships>''')
        archive.writestr('ppt/presProps.xml', f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:presentationPr xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}"/>''')
        archive.writestr('ppt/viewProps.xml', f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:viewPr xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}"><p:normalViewPr/><p:slideViewPr/><p:notesTextViewPr/><p:gridSpacing cx="72008" cy="72008"/></p:viewPr>''')
        archive.writestr('ppt/tableStyles.xml', f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:tblStyleLst xmlns:a="{A_NS}" def="{{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}}"/>''')
        archive.writestr('ppt/slideMasters/slideMaster1.xml', f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldMaster xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}">
  <p:cSld name="Office Theme"><p:bg><p:bgPr><a:solidFill><a:schemeClr val="bg1"/></a:solidFill><a:effectLst/></p:bgPr></p:bg><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>
  <p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>
  <p:sldLayoutIdLst><p:sldLayoutId id="1" r:id="rId1"/></p:sldLayoutIdLst>
  <p:txStyles><p:titleStyle/><p:bodyStyle/><p:otherStyle/></p:txStyles>
</p:sldMaster>''')
        archive.writestr('ppt/slideMasters/_rels/slideMaster1.xml.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="../theme/theme1.xml"/>
</Relationships>''')
        archive.writestr('ppt/slideLayouts/slideLayout1.xml', f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sldLayout xmlns:a="{A_NS}" xmlns:r="{R_NS}" xmlns:p="{P_NS}" type="blank" preserve="1">
  <p:cSld name="Blank"><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>
  <p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sldLayout>''')
        archive.writestr('ppt/slideLayouts/_rels/slideLayout1.xml.rels', '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster" Target="../slideMasters/slideMaster1.xml"/>
</Relationships>''')
        archive.writestr('ppt/theme/theme1.xml', f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<a:theme xmlns:a="{A_NS}" name="Office Theme">
  <a:themeElements>
    <a:clrScheme name="Office">
      <a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>
      <a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>
      <a:dk2><a:srgbClr val="1F1F1F"/></a:dk2>
      <a:lt2><a:srgbClr val="EEECE1"/></a:lt2>
      <a:accent1><a:srgbClr val="4F81BD"/></a:accent1>
      <a:accent2><a:srgbClr val="C0504D"/></a:accent2>
      <a:accent3><a:srgbClr val="9BBB59"/></a:accent3>
      <a:accent4><a:srgbClr val="8064A2"/></a:accent4>
      <a:accent5><a:srgbClr val="4BACC6"/></a:accent5>
      <a:accent6><a:srgbClr val="F79646"/></a:accent6>
      <a:hlink><a:srgbClr val="0000FF"/></a:hlink>
      <a:folHlink><a:srgbClr val="800080"/></a:folHlink>
    </a:clrScheme>
    <a:fontScheme name="Office"><a:majorFont><a:latin typeface="Aptos Display"/></a:majorFont><a:minorFont><a:latin typeface="Aptos"/></a:minorFont></a:fontScheme>
    <a:fmtScheme name="Office"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme>
  </a:themeElements>
  <a:objectDefaults/><a:extraClrSchemeLst/>
</a:theme>''')
    return mem.getvalue()


def build_updated_pptx(existing_blob: bytes | None, title: str, slides: List[DeckSlide]) -> bytes:
    existing_slides = extract_pptx_slides(existing_blob or b'')
    combined = existing_slides + slides if existing_slides else slides
    return build_pptx(title, combined)
