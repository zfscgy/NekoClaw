# python-docx 详细 API 参考

## 1. 样式系统

### 使用内置样式

```python
# 查看文档中所有可用样式
for style in doc.styles:
    print(style.name, style.type)

# 常用内置样式名
# 段落样式：Normal, Heading 1~9, Title, Subtitle
#           List Bullet, List Bullet 2, List Number, List Number 2
#           Quote, Intense Quote, Caption
# 字符样式：Default Paragraph Font, Bold, Emphasis, Strong
# 表格样式：Table Grid, Light List, Light Shading, Medium List 1
```

### 修改内置样式（全局生效）

```python
style = doc.styles["Normal"]
style.font.name = "Times New Roman"
style.font.size = Pt(12)
# 同时设置中文字体
from docx.oxml.ns import qn
rPr = style.element.get_or_add_rPr()
rFonts = rPr.get_or_add_rFonts()
rFonts.set(qn("w:eastAsia"), "宋体")
```

---

## 2. 段落格式详解

```python
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.shared import Pt, Cm

pf = paragraph.paragraph_format

# 对齐方式
pf.alignment = WD_ALIGN_PARAGRAPH.LEFT       # 左对齐
pf.alignment = WD_ALIGN_PARAGRAPH.CENTER     # 居中
pf.alignment = WD_ALIGN_PARAGRAPH.RIGHT      # 右对齐
pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY    # 两端对齐（中文正文推荐）

# 缩进
pf.left_indent = Pt(0)
pf.right_indent = Pt(0)
pf.first_line_indent = Pt(24)    # 首行缩进 2 字符（12pt 字体时）
# pf.first_line_indent = Pt(-24) # 悬挂缩进

# 间距
pf.space_before = Pt(12)         # 段前
pf.space_after  = Pt(6)          # 段后

# 行距
pf.line_spacing = 1.5                              # 多倍行距
pf.line_spacing = Pt(20)                           # 固定行距
pf.line_spacing_rule = WD_LINE_SPACING.EXACTLY     # 固定值
pf.line_spacing_rule = WD_LINE_SPACING.AT_LEAST    # 最小值
pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE    # 多倍

# 分页控制
pf.keep_together = True          # 段落不跨页
pf.keep_with_next = True         # 与下段同页（标题常用）
pf.page_break_before = True      # 段前分页
```

---

## 3. 字体详解

```python
from docx.shared import Pt, RGBColor

run = paragraph.add_run("文字")
f = run.font

# 基本属性
f.name = "Times New Roman"    # 西文字体
f.size = Pt(12)
f.bold = True
f.italic = True
f.underline = True
f.strike = True               # 删除线
f.subscript = True            # 下标
f.superscript = True          # 上标

# 颜色
f.color.rgb = RGBColor(0x1F, 0x49, 0x7D)   # 深蓝色
f.color.theme_color = ...                    # 主题色（不推荐）

# 字符间距
f.cs_bold = True              # 复杂文字（中文）加粗
f.cs_italic = True            # 复杂文字斜体
```

### 正确设置中文字体的完整函数

```python
from docx.oxml.ns import qn

def set_run_font(run, font="宋体", size=12, bold=False, italic=False,
                 color: RGBColor | None = None):
    """设置字体：同时设置西文和东亚字体，并清除主题字体覆盖。"""
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    if color:
        run.font.color.rgb = color
    rFonts = run._r.get_or_add_rPr().get_or_add_rFonts()
    rFonts.set(qn("w:ascii"),    font)
    rFonts.set(qn("w:hAnsi"),    font)
    rFonts.set(qn("w:eastAsia"), font)
    # Heading/List 样式含 w:asciiTheme 等主题字体属性，优先级高于显式字体，必须删除
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme"):
        rFonts.attrib.pop(qn(attr), None)
```

### 标题字体统一设置

```python
def style_heading(doc, level: int, font="宋体", size=16, color=None):
    """统一修改某级标题样式（同时清除主题字体覆盖）"""
    style = doc.styles[f"Heading {level}"]
    style.font.name = font
    style.font.size = Pt(size)
    if color:
        style.font.color.rgb = color
    rPr = style.element.get_or_add_rPr()
    rFonts = rPr.get_or_add_rFonts()
    rFonts.set(qn("w:ascii"),    font)
    rFonts.set(qn("w:hAnsi"),    font)
    rFonts.set(qn("w:eastAsia"), font)
    # Heading 样式默认含 w:asciiTheme="majorHAnsi"，优先级高于显式字体名，必须删除
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme"):
        rFonts.attrib.pop(qn(attr), None)
    pf = style.paragraph_format
    pf.space_before = Pt(12)
    pf.space_after  = Pt(6)
    pf.keep_with_next = True
```

---

## 4. 表格详解

### 创建与基本设置

```python
table = doc.add_table(rows=4, cols=3)
table.style = "Table Grid"

# 常用表格样式
# "Table Grid"         - 全边框（最常用）
# "Light List"         - 无竖线
# "Light Shading"      - 隔行底纹
# "Medium List 1"      - 粗外框
# "Medium Shading 1"   - 阴影效果
```

### 单元格内容与格式

```python
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_ALIGN_VERTICAL

# 写入文字
cell = table.cell(row, col)
cell.text = "内容"

# 段落格式（清除默认并重新设置）
cell.paragraphs[0].clear()
p = cell.paragraphs[0]
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run("内容")
set_run_font(run, zh="宋体", size=11)

# 单元格垂直对齐
from docx.oxml.ns import qn
tc = cell._tc
tcPr = tc.get_or_add_tcPr()
vAlign = OxmlElement("w:vAlign")
vAlign.set(qn("w:val"), "center")  # top / center / bottom
tcPr.append(vAlign)
```

### 设置列宽

```python
from docx.shared import Cm
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

def set_col_width(table, col_idx, width_cm):
    for row in table.rows:
        cell = row.cells[col_idx]
        tc = cell._tc
        tcPr = tc.get_or_add_tcPr()
        tcW = tcPr.get_or_add_tcW()
        tcW.set(qn("w:w"), str(int(Cm(width_cm).emu / 914.4)))  # EMU → 缇
        tcW.set(qn("w:type"), "dxa")
```

### 合并单元格

```python
# 合并 (0,0) 到 (0,2)：第一行三列合并
cell_a = table.cell(0, 0)
cell_b = table.cell(0, 2)
cell_a.merge(cell_b)
```

### 表头行底纹

```python
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

def shade_cell(cell, fill_hex="1F497D"):
    tc = cell._tc
    tcPr = tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill_hex)
    tcPr.append(shd)
```

---

## 5. 列表详情

```python
# 一级无序列表
doc.add_paragraph("项目1", style="List Bullet")

# 二级无序列表（缩进更深）
doc.add_paragraph("子项目", style="List Bullet 2")

# 一级有序列表
doc.add_paragraph("第一步", style="List Number")

# 续接有序列表（不重新编号）
doc.add_paragraph("第二步", style="List Number")
```

---

## 6. 图片

```python
from docx.shared import Cm

# 指定宽度（高度等比缩放）
doc.add_picture("image.png", width=Cm(10))

# 指定高度
doc.add_picture("image.png", height=Cm(5))

# 居中图片（图片默认插入为行内图，通过段落对齐居中）
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = p.add_run()
run.add_picture("image.png", width=Cm(12))
```

---

## 7. 分页、分节与多栏

```python
# 手动分页
doc.add_page_break()

# 分节符（下一页）
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
def add_section_break(doc):
    p = doc.add_paragraph()
    pPr = p._p.get_or_add_pPr()
    sectPr = OxmlElement("w:sectPr")
    type_elem = OxmlElement("w:type")
    type_elem.set(qn("w:val"), "nextPage")  # evenPage / oddPage / continuous
    sectPr.append(type_elem)
    pPr.append(sectPr)
```

---

## 8. 页眉页脚进阶

### 首页不同页眉

```python
section = doc.sections[0]
section.different_first_page_header_footer = True

# 首页页眉
first_header = section.first_page_header
first_header.paragraphs[0].text = "封面，无页眉"

# 其余页页眉
header = section.header
header.paragraphs[0].text = "文档标题"
```

### 页码格式 "第 X 页 共 Y 页"

```python
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

def add_page_number(paragraph):
    """在段落中插入 '第 X 页 共 Y 页' 域代码"""
    def _make_fld(instr):
        run = paragraph.add_run()
        begin = OxmlElement("w:fldChar"); begin.set(qn("w:fldCharType"), "begin")
        instr_el = OxmlElement("w:instrText"); instr_el.text = f" {instr} "
        end = OxmlElement("w:fldChar"); end.set(qn("w:fldCharType"), "end")
        for el in (begin, instr_el, end):
            run._r.append(el)
        return run

    paragraph.add_run("第 ")
    _make_fld("PAGE")
    paragraph.add_run(" 页  共 ")
    _make_fld("NUMPAGES")
    paragraph.add_run(" 页")
```

---

## 9. 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|---------|
| 中文显示宋体但期望微软雅黑 | 只设置了 `font.name`，未设置 `eastAsia` | 用 `set_run_font()` 同时设置西文和东亚字体 |
| 有序列表编号不连续 | 两段列表之间插入了非列表段落 | 检查 style，或用 `numId` 保持同一列表实例 |
| 表格边框消失 | 单元格格式覆盖了表格样式 | 在 `tcPr` 中显式设置边框 XML |
| 首行缩进在标题中出现 | 修改了 Normal 样式的 `first_line_indent` 并被继承 | 在标题样式中显式将首行缩进设为 `None` 或 `Pt(0)` |
| 图片插入后左对齐 | 图片段落默认左对齐 | 将图片所在段落的 `alignment` 设为 CENTER |
