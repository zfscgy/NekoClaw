---
name: create-docx
description: Create Word documents (.docx) programmatically using python-docx. Handles headings, fonts, paragraph formatting, tables, lists, page layout, headers/footers, and Chinese typography. Use when asked to create, generate, or build a Word/DOCX file, or when working on document automation scripts.
---

# Word 文档生成技能（python-docx）

**依赖：`python-docx>=1.0.0`**（`pip install python-docx`）

## 参考脚本

🚨 **必须主动使用 Read 工具读取以下文件**，获取代码结构参考：

| 文件 | 说明 |
|------|------|
| [scripts/docx-example.py](scripts/docx-example.py) | 完整示例：多级标题、正文、表格、列表、页眉页脚 |
| [reference.md](reference.md) | 详细 API 手册（表格、列表、图片、页面设置等） |

---

## 零、两种字体方案（先选方案）

### 方案 A：使用模板文件（推荐）

在 Word 里把所有样式字体手动设好，保存为 `template.docx`，代码加载时直接继承：

```python
doc = Document("template.docx")   # 样式字体已就绪，无需任何 XML 操作
doc.add_heading("标题", level=1)   # 直接使用，字体正确
```

优点：零 XML、可视化调样式、最可靠。
缺点：需要维护一个 `template.docx` 文件。

### 方案 B：纯代码设置字体（无模板）

`Document()` 默认模板的 Heading/List 样式使用 **主题字体**（`w:asciiTheme="majorHAnsi"`），优先级高于 `font.name`，必须用 XML 显式清除后才能生效。详见下文及 [scripts/docx-example.py](scripts/docx-example.py)。

> **官方文档只覆盖了 `font.name = 'Calibri'` 这种基本用法，CJK 字体处理不在官方文档范围内。**

---

## 一、设计原则

### 标题层级
- 使用内置样式 `Heading 1`～`Heading 3`，不要用加粗正文模拟标题
- 标题层级严格对应内容结构，避免跳级（如 H1 → H3）
- 一级标题（H1）一般每章一个；二级（H2）为小节；三级（H3）为细分内容

### 中文字体规范
- **中文**首选 `宋体`（正式文档）或 `微软雅黑`（现代风格）
- **英文/数字**首选 `Times New Roman`（正式）或 `Calibri`（现代）
- python-docx 需同时设置 `font.name`（西文）和 `font.element` 的东亚字体（`eastAsia`），否则中文会回退到系统默认字体
- 正文字号：**12pt**；一级标题：**16–18pt**；二级标题：**14pt**；三级标题：**12pt Bold**

### 段落格式
- 中文正文段落首行缩进 2 字符：`paragraph_format.first_line_indent = Pt(24)`（12pt × 2）
- 段前段后间距：标题段前 `12pt`、段后 `6pt`；正文段后 `6pt`
- 行距：中文正文推荐 `1.5` 倍行距（`line_spacing = 1.5`）
- 正文默认对齐：`WD_ALIGN_PARAGRAPH.JUSTIFY`（两端对齐）

### 页面设置
- A4 纸（默认）：`21 × 29.7 cm`
- 标准页边距：上下 `2.54 cm`，左右 `3.17 cm`（Word 默认值）

---

## 二、API 快速参考

### 初始化与保存

```python
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

doc = Document()
doc.save("output.docx")
```

### 页面设置

```python
from docx.shared import Cm
section = doc.sections[0]
section.page_width  = Cm(21)
section.page_height = Cm(29.7)
section.top_margin    = Cm(2.54)
section.bottom_margin = Cm(2.54)
section.left_margin   = Cm(3.17)
section.right_margin  = Cm(3.17)
```

### 添加标题

```python
# 内置标题样式 "Heading 1"/"Heading 2"/"Heading 3"
h1 = doc.add_heading("第一章 概述", level=1)
h2 = doc.add_heading("1.1 背景", level=2)
h3 = doc.add_heading("1.1.1 研究动机", level=3)
```

### 设置中文字体（关键）

有两个坑必须同时处理，否则中文字体不生效：

**坑1：`font.name` 只设置西文**，中文需额外设置 `eastAsia`

**坑2：Heading / List 样式含主题字体属性**（`w:asciiTheme="majorHAnsi"` 等），**主题字体优先级高于显式字体名**，必须删除

```python
from docx.oxml.ns import qn

def set_run_font(run, font="宋体", size=12, bold=False, italic=False, color=None):
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
    # 删除主题字体覆盖（Heading 样式必须做这步，否则字体设置完全无效）
    for attr in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme"):
        rFonts.attrib.pop(qn(attr), None)
```

**样式级字体**（标题、列表）：在文档最开始统一调用 `init_styles(doc)` 一次性设置好所有样式字体，之后 `add_heading` / `add_paragraph(style=...)` 无需再处理 run。完整实现见 [scripts/docx-example.py](scripts/docx-example.py)。

### 添加正文段落

```python
p = doc.add_paragraph()
pf = p.paragraph_format
pf.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
pf.first_line_indent = Pt(24)   # 首行缩进 2 字符（12pt 字体）
pf.space_after = Pt(6)
pf.line_spacing = 1.5           # 1.5 倍行距

run = p.add_run("这是正文内容。")
set_font(run, zh_font="宋体", size_pt=12)
```

### 字体颜色与高亮

```python
run.font.color.rgb = RGBColor(0xC0, 0x00, 0x00)  # 红色
run.font.underline = True
run.font.italic = True
```

### 无序列表 / 有序列表

```python
# 无序列表（List Bullet）
doc.add_paragraph("第一项", style="List Bullet")
doc.add_paragraph("第二项", style="List Bullet")

# 有序列表（List Number）
doc.add_paragraph("步骤一", style="List Number")
doc.add_paragraph("步骤二", style="List Number")
```

### 表格

```python
table = doc.add_table(rows=3, cols=3)
table.style = "Table Grid"  # 显示边框

# 写入单元格
table.cell(0, 0).text = "列标题1"
cell = table.cell(1, 0)
p = cell.paragraphs[0]
run = p.add_run("内容")
set_font(run, size_pt=11)
```

详细表格 API（合并单元格、宽度、背景色）→ [reference.md](reference.md)

### 页眉与页脚

```python
section = doc.sections[0]

# 页眉
header = section.header
hp = header.paragraphs[0]
hp.text = "文档标题"
hp.alignment = WD_ALIGN_PARAGRAPH.CENTER

# 页脚（添加页码）
footer = section.footer
fp = footer.paragraphs[0]
fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
run = fp.add_run()
# 插入页码域代码
from docx.oxml import OxmlElement
fldChar = OxmlElement("w:fldChar")
fldChar.set(qn("w:fldCharType"), "begin")
run._r.append(fldChar)
instrText = OxmlElement("w:instrText")
instrText.text = "PAGE"
run._r.append(instrText)
fldChar2 = OxmlElement("w:fldChar")
fldChar2.set(qn("w:fldCharType"), "end")
run._r.append(fldChar2)
```

---

## 三、工作流程

1. **明确文档结构**：标题层级、章节数量、是否有表格/列表
2. **定义字体常量**：在文件顶部统一定义 `ZH_FONT`、`EN_FONT`、字号等
3. **封装 `set_font()` 工具函数**：避免重复 XML 操作
4. **按顺序添加内容**：页面设置 → 标题 → 各章节 → 页眉页脚
5. **保存并验证**：`python your_script.py`，用 Word 打开检查排版

## 详细 API 文档

→ 见 [reference.md](reference.md)
