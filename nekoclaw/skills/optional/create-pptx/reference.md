# python-pptx 详细 API 参考

> 版本：**python-pptx 1.0.2**
> 官方文档：https://python-pptx.readthedocs.io/

---

## 1. 幻灯片基础

### 创建 Presentation

```python
from pptx import Presentation
from pptx.util import Inches, Pt, Emu

prs = Presentation()
# 16:9 宽屏标准尺寸
prs.slide_width  = Inches(13.33)
prs.slide_height = Inches(7.5)

# 可用版式索引（空白版式最常用）
# 0: 标题幻灯片, 1: 标题+内容, 5: 仅标题, 6: 空白
slide = prs.slides.add_slide(prs.slide_layouts[6])
```

### 单位换算

| 单位 | 说明 | 换算 |
|------|------|------|
| `Inches(n)` | 英寸 | 1 inch = 914400 EMU |
| `Pt(n)` | 磅（字号/线宽）| 1 pt = 12700 EMU |
| `Emu(n)` | 原始 EMU，最精确 | — |

---

## 2. 背景与填充

### 幻灯片背景

```python
fill = slide.background.fill
fill.solid()                                    # 纯色
fill.fore_color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
# fill.background()                             # 恢复主题背景
```

### 形状填充（`shape.fill`）

```python
# 纯色填充
shape.fill.solid()
shape.fill.fore_color.rgb = RGBColor(0xE8, 0xF3, 0xFF)

# 无填充（透明）
shape.fill.background()

# 渐变（python-pptx 1.x 支持有限，推荐用纯色）
# shape.fill.gradient()
```

---

## 3. 边框与线条（`shape.line`）

```python
shape.line.color.rgb = RGBColor(0x5B, 0x9B, 0xD5)
shape.line.width = Pt(1.8)        # 线宽，Pt(0.75) 细线，Pt(2.5) 粗线
shape.line.fill.background()      # 无边框（等价于透明线）
shape.shadow.inherit = False       # 关闭继承阴影
```

### 线条虚实样式（需通过 XML 操作）

```python
from pptx.oxml.ns import qn
from lxml import etree

ln = shape.line._get_or_add_ln()
# 虚线
prstDash = etree.SubElement(ln, qn('a:prstDash'))
prstDash.set('val', 'dash')  # 可选: 'dot', 'dash', 'dashDot', 'lgDash', 'sysDash'
```

---

## 4. 形状（Shapes）

### add_shape 参数

```python
shape = slide.shapes.add_shape(
    shape_type,   # int 或 MSO_AUTO_SHAPE_TYPE 枚举
    left,         # Inches / Emu
    top,
    width,
    height,
)
```

### 常用 MSO_AUTO_SHAPE_TYPE 枚举值

```python
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE

MSO_AUTO_SHAPE_TYPE.RECTANGLE            # 1  - 矩形
MSO_AUTO_SHAPE_TYPE.ROUNDED_RECTANGLE    # 5  - 圆角矩形
MSO_AUTO_SHAPE_TYPE.OVAL                 # 9  - 椭圆
MSO_AUTO_SHAPE_TYPE.ISOCELES_TRIANGLE    # 7  - 等腰三角形
MSO_AUTO_SHAPE_TYPE.RIGHT_ARROW          # 33 - 右箭头
MSO_AUTO_SHAPE_TYPE.LEFT_ARROW           # 34 - 左箭头
MSO_AUTO_SHAPE_TYPE.UP_ARROW             # 35 - 上箭头
MSO_AUTO_SHAPE_TYPE.DOWN_ARROW           # 36 - 下箭头
MSO_AUTO_SHAPE_TYPE.PENTAGON             # 56 - 五边形（流程图）
MSO_AUTO_SHAPE_TYPE.CHEVRON              # 55 - 箭形（飘带箭头）
MSO_AUTO_SHAPE_TYPE.FIVE_POINTED_STAR    # 12 - 五角星
```

> **注意**：直接用整数（如 `1`、`33`）与枚举等效，整数更简洁。

### 圆角矩形的圆角调整（adjst）

```python
# 通过 XML 调整圆角大小（0-100000，默认 16667 ≈ 16.7%）
from pptx.oxml.ns import qn
sp = box._element
spPr = sp.find(qn('p:spPr'))
prstGeom = spPr.find(qn('a:prstGeom'))
avLst = prstGeom.find(qn('a:avLst'))
if avLst is None:
    avLst = etree.SubElement(prstGeom, qn('a:avLst'))
gd = etree.SubElement(avLst, qn('a:gd'))
gd.set('name', 'adj')
gd.set('fmla', 'val 8000')  # 8% 圆角
```

---

## 5. 文本框（TextBox）与文本框架（TextFrame）

### 添加文本框

```python
txBox = slide.shapes.add_textbox(left, top, width, height)
tf = txBox.text_frame

tf.word_wrap = True               # 自动换行
tf.auto_size = None               # 不自动调整大小
```

### 垂直对齐（`tf.vertical_anchor`）

```python
from pptx.enum.text import MSO_ANCHOR

tf.vertical_anchor = MSO_ANCHOR.TOP     # 顶部
tf.vertical_anchor = MSO_ANCHOR.MIDDLE  # 垂直居中
tf.vertical_anchor = MSO_ANCHOR.BOTTOM  # 底部
```

### 段落（Paragraph）

```python
p = tf.paragraphs[0]            # 第一个段落（创建时自动存在）
p2 = tf.add_paragraph()         # 追加段落

p.alignment = PP_ALIGN.CENTER   # LEFT / CENTER / RIGHT / JUSTIFY
p.space_before = Pt(4)          # 段前间距
p.space_after  = Pt(6)          # 段后间距
p.level = 0                     # 缩进级别（0-8）
```

### 运行（Run）与字体

```python
run = p.add_run()
run.text = "示例文字"

f = run.font
f.name  = "微软雅黑"             # 中文字体
f.size  = Pt(16)
f.bold  = True
f.italic = False
f.underline = False
f.color.rgb = RGBColor(0x1A, 0x56, 0x9A)
```

### 形状内置文本框（直接操作矩形等形状的文字）

```python
# 对 add_shape 返回的形状直接设置文本
tf = shape.text_frame
tf.clear()    # 清除默认段落
tf.word_wrap = True
tf.vertical_anchor = MSO_ANCHOR.MIDDLE
p = tf.paragraphs[0]
# ... 同上
```

---

## 6. 连接线（Connector）

### 直线 / 分隔线

```python
from pptx.enum.shapes import MSO_CONNECTOR

line = slide.shapes.add_connector(
    MSO_CONNECTOR.STRAIGHT,
    Inches(0.5),  Inches(1.2),    # 起点 (x1, y1)
    Inches(12.83), Inches(1.2),   # 终点 (x2, y2)
)
line.line.color.rgb = RGBColor(0xC0, 0x00, 0x00)
line.line.width = Pt(2.5)
```

### 连接两个形状（锚点连接）

```python
connector = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, 0, 0, 0, 0)
# 连接点编号：0=上中, 1=右中, 2=下中, 3=左中
connector.begin_connect(shape_a, 2)  # shape_a 底部中心
connector.end_connect(shape_b, 0)    # shape_b 顶部中心
connector.line.color.rgb = RGBColor(0x5B, 0x9B, 0xD5)
connector.line.width = Pt(1.4)
```

### 为连接线添加箭头（XML）

```python
from pptx.oxml import parse_xml

ln = connector.line._get_or_add_ln()

# 末端三角箭头
ln.append(parse_xml(
    '<a:tailEnd type="triangle" w="lg" len="med" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"/>'
))

# 起端箭头（可选）
ln.append(parse_xml(
    '<a:headEnd type="triangle" w="med" len="med" '
    'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"/>'
))

# type 选项: "none"(无), "triangle", "stealth"(细箭头), "diamond", "oval", "arrow"
# w/len 选项: "sm", "med", "lg"
```

---

## 7. 图片

```python
from pptx.util import Inches

pic = slide.shapes.add_picture(
    "path/to/image.png",
    left=Inches(1.0),
    top=Inches(1.5),
    width=Inches(3.0),   # 省略 height 则等比缩放
)
```

---

## 8. 颜色

```python
from pptx.dml.color import RGBColor

# 十六进制
color = RGBColor(0x1A, 0x56, 0x9A)

# 也可以从 hex 字符串
color = RGBColor.from_string("1A569A")   # 不含 #
```

---

## 9. 字体选择建议

| 场景 | 推荐字体 |
|------|---------|
| 中文正文/标题 | 微软雅黑（`Microsoft YaHei`） |
| 中文宋体风格 | 宋体（`SimSun`） |
| 英文正文 | Calibri / Arial |
| 英文标题 | Segoe UI / Helvetica |
| 数字/代码 | Consolas / Courier New |
| 商务报告（中英混排）| 微软雅黑（自动兼容英文） |

> 确保目标机器安装了所选字体，否则 PPT 打开后会回退到默认字体。
> `微软雅黑` 在 Windows 系统上预装，是最安全的中文字体选择。

---

## 10. 常见问题

### 文字溢出框外
- 设置 `tf.word_wrap = True`
- 适当减小 `font.size` 或增大文本框 `height`
- 检查 `tf.auto_size` 是否设置，1.0.2 中设为 `None` 或 `MSO_AUTO_SIZE.NONE`

### 形状没有阴影
```python
shape.shadow.inherit = False
```

### 去除矩形默认黑色边框
```python
shape.line.fill.background()   # 推荐
# 或者
shape.line.color.rgb = shape.fill.fore_color.rgb  # 与填充同色
```

### 移除矩形内文本框内边距
```python
from pptx.util import Emu
tf.margin_left   = Emu(0)
tf.margin_right  = Emu(0)
tf.margin_top    = Emu(0)
tf.margin_bottom = Emu(0)
```

### 获取现有形状位置（调试用）
```python
for shape in slide.shapes:
    print(shape.name, shape.left, shape.top, shape.width, shape.height)
```
