---
name: pptx-skill
description: Create PowerPoint slides programmatically using python-pptx. Generates professional PPT presentations with shapes, text boxes, connectors, arrows, and styled layouts. Use when asked to create, generate, or build a PPT/PowerPoint file, or when working on presentation automation scripts.
---

# PPT 生成技能（python-pptx）

**当前版本：python-pptx 1.0.2**

## 参考脚本

🚨 **必须主动使用 Read 工具读取以下模板文件**，以获得布局和代码结构的参考！
以下脚本涵盖常见布局，**不要照搬其中的颜色/字号等样式**——样式应根据实际需求自行设计：

| 脚本 | 布局类型 | 核心技术要点 |
|------|---------|------------|
| [scripts/pptx-example1-2x2_grid.py](scripts/pptx-example1-2x2_grid.py) | 2×2 卡片网格 | 矩形背景 + 叠加文本框、连接线分隔线 |
| [scripts/pptx-example2-3column.py](scripts/pptx-example2-3column.py) | 3列卡片+箭头 | 图片占位符矩形、实心右箭头（shape 33）|
| [scripts/pptx-example3-3row_companies.py](scripts/pptx-example3-3row_companies.py) | 3行横向布局 | 图片+文字混排、多段落文本（标题/副标题/正文）|
| [scripts/pptx-example4-finetune-flow.py](scripts/pptx-example4-finetune-flow.py) | 竖向流程图 | 圆角矩形、connector 连接线、XML 箭头 tailEnd |

---

## 一、PPT 设计原则

### 📚 资料搜集与篇幅要求（重要）
- **必须搜集充足的资料**：在生成 PPT 之前，务必进行充分的信息检索和内容扩充，确保 PPT 具有实质性内容。
- **默认页数要求**：即使用户没有明确指定页数，也需要主动将 PPT 的内容丰富到 **10 页左右**。绝不能只做两三页敷衍了事。

### 🚨 核心底线：拒绝纯文本，主动设计美观样式
- **绝对不要只用纯文字堆砌来制作 PPT**！即使用户仅仅是简单要求“做个PPT”，你也**必须**主动为其考虑样式、布局等，提供丰富的视觉效果。
- 综合运用背景色块、圆角矩形、卡片式布局、图标占位、线条分隔等元素，让页面看起来专业、现代且视觉层次分明。

### 🎨 色彩与对比度（极其重要）
- **字体颜色与底色之间必须有强烈的对比度**，确保内容清晰易读。**必须是：浅色背景 + 深色字体，或者 深色背景 + 浅色字体**。
- 切忌在深色背景上使用暗色文字，或在浅色背景上使用浅色文字。例如：深色背景必须配纯白或极浅色文字，浅色背景必须配深色文字。
- 颜色方案由用户需求或主题决定，**不要默认照搬示例脚本的配色**。
- 在脚本顶部集中定义颜色常量（`RGBColor`），不要在函数内散落。

### 内容丰富
- 每张 slide 应有清晰的**主标题**区域和主体内容区，二者在视觉上有明显分隔（分隔线、留白或背景色差）
- 文字层次至少 2 级（如标题 + 正文），复杂内容可到 3 级（标题 / 副标题 / 正文）
- 同一层级的字号和样式应保持一致；各层级字号差距至少 4pt，避免层次不清
- 段落之间设置 `space_before`/`space_after` 留白，避免文字密集堆叠

### 布局美观
- 幻灯片尺寸使用 **16:9 宽屏**：`SLIDE_W=13.33, SLIDE_H=7.5`（单位 Inches）
- 使用空白版式 `prs.slide_layouts[6]`，完全自主控制布局
- 所有位置/尺寸用命名常量定义（`MARGIN_X`, `MARGIN_Y`, `GAP`, `CARD_W` 等），通过公式推导，不散落硬编码数字
- 保持一致的内外边距：元素间距和边缘留白在全 slide 内统一

### 字体与文本排版
- **文本框自动换行（必须）**：必须为长文本设置 `tf.word_wrap = True`，防止文字溢出幻灯片边界。
- **指定文本语言（必须）**：每一个 `run` 都必须设置 `run.font.language_id`，**默认中文 `MSO_LANGUAGE_ID.SIMPLIFIED_CHINESE`**。否则 PowerPoint 会把中文当作英文进行拼写校对，导致 PPT 打开后出现大量红色波浪线（误报为拼写错误）。
- 中文首选 `微软雅黑`，英文可选 `Calibri` / `Arial`；混排时一种字体即可兼顾
- 单张 slide 内不超过 2 种字体
- 标题用 Bold，正文用 Regular；慎用 Italic（仅用于提示性、次要信息）
- 字号根据内容密度调整：内容越多，字号应适当缩小，确保不溢出

### 方框与形状
- 方框选型原则：
  - **普通信息块** → 矩形（`type=1`）
  - **流程步骤** → 圆角矩形（`ROUNDED_RECTANGLE`）更柔和
  - 需要文字排版复杂时，用**矩形做背景 + 独立 `add_textbox()` 覆盖**，避免在形状的 `text_frame` 里堆多段落
  - 去除不需要的边框：`shape.line.fill.background()`
  - 去除默认阴影：`shape.shadow.inherit = False`

---

## 二、API 快速参考

### 初始化

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_AUTO_SHAPE_TYPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.lang import MSO_LANGUAGE_ID  # 必须：用于设置文本语言，避免误判校对

prs = Presentation()
prs.slide_width  = Inches(13.33)
prs.slide_height = Inches(7.5)
slide = prs.slides.add_slide(prs.slide_layouts[6])  # 空白版式
prs.save("output.pptx")
```

### 背景色

```python
fill = slide.background.fill
fill.solid()
fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
```

### 矩形（普通 / 圆角）

```python
# 普通矩形 (type=1)
rect = slide.shapes.add_shape(1, left, top, width, height)

# 圆角矩形
rect = slide.shapes.add_shape(
    MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE, left, top, width, height
)

rect.fill.solid()
rect.fill.fore_color.rgb = RGBColor(0xF2, 0xF2, 0xF2)
rect.line.color.rgb = RGBColor(0xD0, 0xD0, 0xD0)
rect.line.width = Pt(0.75)
rect.line.fill.background()  # 无边框
rect.shadow.inherit = False   # 去除阴影
```

### 文本框

```python
txBox = slide.shapes.add_textbox(left, top, width, height)
tf = txBox.text_frame
tf.word_wrap = True
tf.vertical_anchor = MSO_ANCHOR.MIDDLE  # 垂直居中

p = tf.paragraphs[0]
p.alignment = PP_ALIGN.CENTER
p.space_after = Pt(6)
run = p.add_run()
run.text = "内容"
run.font.name = "微软雅黑"
run.font.size = Pt(16)
run.font.bold = True
run.font.color.rgb = RGBColor(0x1A, 0x56, 0x9A)
run.font.language_id = MSO_LANGUAGE_ID.SIMPLIFIED_CHINESE  # 关键：指定中文，避免校对误报

# 追加段落
p2 = tf.add_paragraph()
p2.space_before = Pt(4)
run2 = p2.add_run()
run2.text = "正文内容"
run2.font.size = Pt(14)
run2.font.language_id = MSO_LANGUAGE_ID.SIMPLIFIED_CHINESE
```

> 💡 **建议**：在脚本中封装一个 `make_run(p, text, ...)` 辅助函数，统一在内部设置 `language_id`，避免遗漏。参见示例脚本中的 `add_run` / 文本辅助函数实现。

### 分隔线（Connector）

```python
line = slide.shapes.add_connector(
    MSO_CONNECTOR.STRAIGHT,
    Inches(0.5), Inches(1.2),   # 起点 x, y
    Inches(12.83), Inches(1.2), # 终点 x, y
)
line.line.color.rgb = RGBColor(0xC0, 0x00, 0x00)
line.line.width = Pt(2.5)
```

### 箭头形状

```python
# 实心右箭头（shape type=33）
arrow = slide.shapes.add_shape(33, left, top, width, height)
arrow.fill.solid()
arrow.fill.fore_color.rgb = RGBColor(0x1A, 0x56, 0x9A)
arrow.line.color.rgb = RGBColor(0x1A, 0x56, 0x9A)  # 无边框效果

# 向下箭头（shape type=36）
arrow = slide.shapes.add_shape(36, left, top, width, height)
```

### 连接线带箭头（XML方式）

```python
from pptx.oxml import parse_xml

connector = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, 0, 0, 0, 0)
connector.begin_connect(upper_shape, 2)  # 连接点: 0=上中, 1=右中, 2=下中, 3=左中
connector.end_connect(lower_shape, 0)
connector.line.color.rgb = RGBColor(0x5B, 0x9B, 0xD5)
connector.line.width = Pt(1.4)

ln = connector.line._get_or_add_ln()
ln.append(parse_xml(
    '<a:tailEnd type="triangle" w="lg" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"/>'
))
```

---

## 三、常用 shape type 数值

| 类型 | 数值 / 枚举 |
|------|-----------|
| 矩形 | `1` |
| 圆角矩形 | `MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE`（= 5）|
| 右箭头 | `33` |
| 左箭头 | `34` |
| 上箭头 | `35` |
| 下箭头 | `36` |
| 五角星 | `12` |
| 椭圆 | `9` |

---

## 四、工作流程

### 🏗️ 大型 PPT 项目结构（多文件组织）
- 如果 PPT 内容较多（如达到 10 页及以上），**必须单独创建一个 Python 项目**来管理，不要把所有代码塞进一个巨大的 `.py` 文件中。
- **多文件拆分**：将代码拆分为多个模块，例如每个文件负责生成 1 页或特定几页（如 `slide_01_intro.py`, `slide_02_market.py`），然后由一个主入口文件（如 `main.py`）统一调用和组装。

### 步骤指南
1. **确认内容结构**：明确每张 slide 的标题、主体布局类型（网格 / 列 / 行 / 流程图）
2. **选择参考脚本**：从上方表格选取最接近的布局，Read 对应脚本
3. **定义颜色/字体常量**：在文件顶部统一定义，不散落在函数内
4. **计算布局尺寸**：用公式推导 `CARD_W`/`CARD_H`，避免魔法数字
5. **分层绘制**：先背景矩形 → 再图片占位 → 最后文本框（从底层到顶层）
6. **运行验证**：`python your_script.py` 后用 PPT 打开检查

## 详细 API 文档

→ 详见 [reference.md](reference.md)
