"""Generate the Word manual from the adjacent, human-editable Markdown source."""
from pathlib import Path
import re
from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'doc' / 'Agent MCP Proxy 软件用户使用手册.md'
OUTPUT = SOURCE.with_suffix('.docx')
BLUE = '155B8D'
FONT = 'PingFang SC'


def font(style, size, bold=False, color=None):
    style.font.name = 'Arial'
    style.font.size = Pt(size)
    style.font.bold = bold
    style.element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'), FONT)
    if color:
        style.font.color.rgb = RGBColor.from_string(color)


def inline(paragraph, text):
    pattern = r'(\*\*.*?\*\*|`[^`]+`|\[[^\]]+\]\(<[^>]+>\))'
    for part in re.split(pattern, text):
        if not part:
            continue
        if part.startswith('**') and part.endswith('**'):
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith('`') and part.endswith('`'):
            run = paragraph.add_run(part[1:-1])
            run.font.name = 'Menlo'
            run.font.size = Pt(9)
        elif match := re.fullmatch(r'\[([^\]]+)\]\(<([^>]+)>\)', part):
            link = OxmlElement('w:hyperlink')
            rid = paragraph.part.relate_to(match[2], 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink', is_external=True)
            link.set(qn('r:id'), rid)
            run = OxmlElement('w:r')
            props = OxmlElement('w:rPr')
            color = OxmlElement('w:color'); color.set(qn('w:val'), BLUE); props.append(color)
            run.append(props)
            content = OxmlElement('w:t'); content.text = match[1]; run.append(content)
            link.append(run); paragraph._p.append(link)
        else:
            paragraph.add_run(part)


def shade(cell, fill):
    shading = OxmlElement('w:shd'); shading.set(qn('w:fill'), fill)
    cell._tc.get_or_add_tcPr().append(shading)


def main():
    document = Document()
    section = document.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(1.9)
    section.bottom_margin = Cm(1.9)
    section.left_margin = Cm(2.0)
    section.right_margin = Cm(2.0)
    section.header_distance = Cm(0.8)
    section.footer_distance = Cm(0.8)
    styles = document.styles
    font(styles['Normal'], 10.5)
    styles['Normal'].paragraph_format.line_spacing = 1.25
    styles['Normal'].paragraph_format.space_after = Pt(6)
    font(styles['Title'], 29, True, BLUE)
    font(styles['Subtitle'], 18, False, BLUE)
    for name, size in [('Heading 1', 17), ('Heading 2', 12.5), ('Heading 3', 11)]:
        font(styles[name], size, True, BLUE)
        styles[name].paragraph_format.space_before = Pt(14)
        styles[name].paragraph_format.space_after = Pt(8)
        styles[name].paragraph_format.keep_with_next = True
    for name in ('List Bullet', 'List Number'):
        font(styles[name], 10.5)
        styles[name].paragraph_format.space_after = Pt(5)
    font(styles['Caption'], 9, False, '617386')
    styles['Caption'].paragraph_format.space_after = Pt(10)
    font(styles['Header'], 8.5, False, '617386')
    font(styles['Footer'], 8.5, False, '617386')
    header = section.header.paragraphs[0]
    header.text = 'Agent MCP Proxy  |  软件用户使用手册'
    footer = section.footer.paragraphs[0]
    footer.alignment = 2
    footer.add_run('用户手册 1.0  ·  第 ')
    field = OxmlElement('w:fldSimple'); field.set(qn('w:instr'), 'PAGE'); footer._p.append(field)
    footer.add_run(' 页')
    document.core_properties.title = 'Agent MCP Proxy 软件用户使用手册'
    document.core_properties.subject = '当前管理版的登录、服务配置、设备授权、查询分页和故障处理'
    document.core_properties.author = 'Agent MCP Proxy'
    document.core_properties.keywords = 'MCP,小智,用户手册,工具授权,服务目录'

    cover = document.add_paragraph(style='Title')
    cover.paragraph_format.space_before = Cm(4)
    cover.add_run('Agent MCP Proxy')
    document.add_paragraph('软件用户使用手册', style='Subtitle')
    document.add_paragraph('登录 · 接入服务 · 授权设备 · 日常维护')
    document.add_paragraph('文档版本 1.0\n编制日期 2026 年 10 月 24 日\n适用当前设备与 MCP 服务目录管理版')
    note = document.add_paragraph('所有界面插图均使用模拟数据，不包含真实凭据；模拟连接状态不代表实际设备验收结果。')
    note.paragraph_format.space_before = Cm(2)
    document.add_page_break()

    lines = SOURCE.read_text(encoding='utf-8').splitlines()
    content_start = next(i for i, line in enumerate(lines) if line == '## 阅读导航')
    lines = lines[content_start:]
    figure = 0
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1; continue
        if line.startswith('## '):
            heading = line[3:]
            if heading == '1. 软件概述': document.add_page_break()
            document.add_heading(heading, level=1)
            i += 1; continue
        if line.startswith('### '):
            document.add_heading(line[4:], level=2)
            i += 1; continue
        if line.startswith('```'):
            code = []
            i += 1
            while i < len(lines) and not lines[i].startswith('```'):
                code.append(lines[i]); i += 1
            paragraph = document.add_paragraph()
            paragraph.paragraph_format.line_spacing = 1.05
            paragraph.paragraph_format.keep_together = True
            for index, row in enumerate(code):
                run = paragraph.add_run(row + ('\n' if index < len(code)-1 else ''))
                run.font.name = 'Menlo'; run.font.size = Pt(8.5)
            shading = OxmlElement('w:shd'); shading.set(qn('w:fill'), 'F2F5F8')
            paragraph._p.get_or_add_pPr().append(shading)
            i += 1; continue
        if line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                row = [value.strip() for value in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?', value) for value in row): rows.append(row)
                i += 1
            columns = len(rows[0])
            table = document.add_table(rows=0, cols=columns)
            table.style = 'Table Grid'; table.autofit = False
            widths = [5.0, 12.0] if columns == 2 else [3.2, 7.2, 6.6]
            for row_number, row in enumerate(rows):
                cells = table.add_row().cells
                no_split = OxmlElement('w:cantSplit'); table.rows[-1]._tr.get_or_add_trPr().append(no_split)
                for col, value in enumerate(row):
                    cells[col].width = Cm(widths[col])
                    p = cells[col].paragraphs[0]
                    p.paragraph_format.space_after = Pt(4)
                    p.paragraph_format.space_before = Pt(4)
                    inline(p, value)
                    for run in p.runs:run.font.size = Pt(9)
                    if row_number == 0:
                        shade(cells[col], 'E8F0F7')
                        for run in p.runs:run.bold = True
                if row_number == 0:
                    repeat = OxmlElement('w:tblHeader'); table.rows[0]._tr.get_or_add_trPr().append(repeat)
            for col, width in zip(table.columns, widths):col.width = Cm(width)
            document.add_paragraph().paragraph_format.space_after = Pt(0)
            continue
        if image := re.fullmatch(r'!\[([^\]]*)\]\(<([^>]+)>\)', line):
            source = SOURCE.parent / image[2]
            with Image.open(source) as bitmap:w, h = bitmap.size
            width_cm = min(16.5, 18.5*w/h)
            p = document.add_paragraph()
            p.alignment = 1
            p.paragraph_format.keep_with_next = True
            p.add_run().add_picture(str(source), width=Cm(width_cm))
            figure += 1
            p = document.add_paragraph(f'图 {figure}  {image[1]}', style='Caption')
            p.alignment = 1
            i += 1; continue
        if line.startswith('- [ ] '):
            p = document.add_paragraph(style='List Bullet'); inline(p, '□ ' + line[6:])
        elif line.startswith('- '):
            p = document.add_paragraph(style='List Bullet'); inline(p, line[2:])
        elif re.match(r'^\d+\. ', line):
            # Keep authored step numbers; Word automatic list numbering can otherwise continue across sections.
            p = document.add_paragraph(); p.paragraph_format.left_indent = Cm(0.35); inline(p, line)
        elif line.startswith('> '):
            p = document.add_paragraph(); inline(p, line[2:])
        else:
            p = document.add_paragraph(); inline(p, line)
        i += 1
    document.save(OUTPUT)
    result = Document(OUTPUT)
    assert len(result.inline_shapes) == 5
    assert len(result.tables) >= 12
    assert sum(p.style.name == 'Heading 1' for p in result.paragraphs) == 18
    print('Created:', OUTPUT)
    print('Content:', len(result.paragraphs), 'paragraphs,', len(result.tables), 'tables,', len(result.inline_shapes), 'figures')


if __name__ == '__main__':
    main()
