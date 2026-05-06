---
name: read-office-files
description: 读取并解析 .docx 和 .pptx 文件内容，提取文本、表格、元数据和演讲者备注，输出结构化 Markdown 或 JSON。当用户需要读取、解析、提取或分析 Word 文档（docx）或 PowerPoint 演示文稿（pptx）内容时使用，包括提取文字、标题结构、表格数据、幻灯片内容、备注信息等场景。
---

# 读取 Office 文件（docx / pptx）

**依赖：`python-docx>=1.0.0`、`python-pptx>=1.0.2`**
```bash
pip install python-docx python-pptx
```

## 工具脚本

直接执行脚本，无需额外编码：

| 脚本 | 用途 |
|------|------|
| [scripts/read-docx.py](scripts/read-docx.py) | 读取 .docx：段落、标题层级、表格、元数据 |
| [scripts/read-pptx.py](scripts/read-pptx.py) | 读取 .pptx：逐张幻灯片标题/正文、表格、备注、元数据 |

---

## 一、读取 DOCX

### 命令行快速使用

```bash
# 基础：输出 Markdown 格式文本
python scripts/read-docx.py input.docx

# 同时提取表格 + 元数据
python scripts/read-docx.py input.docx --tables --meta

# JSON 格式（供程序处理）
python scripts/read-docx.py input.docx --json --tables --meta
```

### 在脚本中调用

```python
from docx import Document

doc = Document("input.docx")

# 遍历所有段落（含标题）
for para in doc.paragraphs:
    print(para.style.name, para.text)   # e.g. "Heading 1", "第一章"

# 提取所有表格
for table in doc.tables:
    for row in table.rows:
        cells = [cell.text for cell in row.cells]
        print(cells)

# 元数据
cp = doc.core_properties
print(cp.title, cp.author, cp.created)
```

### 判断标题层级

```python
def heading_level(para) -> int | None:
    name = para.style.name          # "Heading 1" / "Heading 2" / "Normal" ...
    for lvl in (1, 2, 3, 4):
        if name == f"Heading {lvl}":
            return lvl
    return None
```

---

## 二、读取 PPTX

### 命令行快速使用

```bash
# 基础：逐张输出幻灯片标题 + 正文
python scripts/read-pptx.py input.pptx

# 同时提取表格、备注、元数据
python scripts/read-pptx.py input.pptx --tables --notes --meta

# JSON 格式
python scripts/read-pptx.py input.pptx --json --tables --notes --meta
```

### 在脚本中调用

```python
from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER

prs = Presentation("input.pptx")

for i, slide in enumerate(prs.slides, 1):
    title = ""
    body_lines = []

    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        # 判断是否为标题占位符
        if shape.is_placeholder:
            ph_type = shape.placeholder_format.type
            if ph_type in (PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE):
                title = shape.text_frame.text.strip()
                continue
        # 正文文本
        for para in shape.text_frame.paragraphs:
            text = para.text.strip()
            if text:
                body_lines.append(text)

    print(f"Slide {i}: {title}")
    for line in body_lines:
        print(f"  - {line}")
```

### 提取演讲者备注

```python
for slide in prs.slides:
    if slide.has_notes_slide:
        notes = slide.notes_slide.notes_text_frame.text.strip()
        if notes:
            print(notes)
```

### 提取幻灯片中的表格

```python
for shape in slide.shapes:
    if shape.has_table:
        for row in shape.table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            print(cells)
```

---

## 三、JSON 输出格式参考

**DOCX JSON 结构：**

```json
{
  "meta": { "title": "...", "author": "...", "created": "..." },
  "paragraphs": [
    { "type": "heading", "level": 1, "text": "第一章" },
    { "type": "paragraph", "level": null, "text": "正文内容..." }
  ],
  "tables": [
    [["列A", "列B"], ["值1", "值2"]]
  ]
}
```

**PPTX JSON 结构：**

```json
{
  "meta": { "title": "...", "author": "...", "slides": 10 },
  "slides": [
    {
      "slide": 1,
      "title": "幻灯片标题",
      "body": ["要点一", "要点二"],
      "tables": [[ ["列A", "列B"], ["值1", "值2"] ]],
      "notes": "演讲者备注文本"
    }
  ]
}
```

---

## 四、工作流程

1. **确认文件类型**：`.docx` → 用 `read-docx.py`；`.pptx` → 用 `read-pptx.py`
2. **选择输出格式**：
   - 供人阅读 → 默认（Markdown）
   - 供程序处理 → 加 `--json`
3. **按需开启选项**：`--tables`（提取表格）、`--notes`（pptx 备注）、`--meta`（元数据）
4. **在代码中使用**：直接参考上方 API 片段，或用 `--json` 管道接收结构化数据
