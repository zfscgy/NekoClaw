"""
read-any.py  —  通用文件转 Markdown（基于 microsoft/markitdown）

用途：
    把任意常见格式的文件（.pdf / .pptx / .docx / .xlsx / .html / .csv /
    .json / .xml / .epub / 图片 / 音频 / .zip 等）读成 Markdown 纯文本，
    直接打印到 stdout，并默认截断超长内容，避免一次塞爆 LLM 上下文。

用法:
    python read-any.py <file> [--max-chars N] [--no-truncate]

选项:
    --max-chars N    最大输出字符数（默认 20000）。超出部分截断并提示。
    --no-truncate    关闭截断，完整输出（慎用，可能很长）。

依赖:
    pip install "markitdown[all]"
"""

import sys
import argparse
from pathlib import Path

try:
    from markitdown import MarkItDown
except ImportError:
    print(
        '[错误] 未安装 markitdown，请运行：pip install "markitdown[all]"',
        file=sys.stderr,
    )
    sys.exit(2)


DEFAULT_MAX_CHARS = 20000


def convert(path: Path) -> str:
    md = MarkItDown()
    result = md.convert(str(path))
    return result.text_content


def truncate(text: str, max_chars: int) -> str:
    """超过阈值则截断，并附上提示。"""
    if len(text) <= max_chars:
        return text
    omitted = len(text) - max_chars
    notice = (
        f"\n\n... [已截断 {omitted} 字符 / 共 {len(text)} 字符，"
        f"如需完整内容请加 --no-truncate 或调高 --max-chars] ..."
    )
    return text[:max_chars] + notice


def main():
    parser = argparse.ArgumentParser(
        description="把任意常见格式的文件转换为 Markdown 文本（默认截断）"
    )
    parser.add_argument("file", help="要读取的文件路径")
    parser.add_argument(
        "--max-chars", type=int, default=DEFAULT_MAX_CHARS,
        help=f"最大输出字符数，默认 {DEFAULT_MAX_CHARS}",
    )
    parser.add_argument(
        "--no-truncate", action="store_true",
        help="关闭截断，完整输出",
    )
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"[错误] 文件不存在：{path}", file=sys.stderr)
        sys.exit(1)

    text = convert(path)
    if not args.no_truncate:
        text = truncate(text, args.max_chars)

    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")


if __name__ == "__main__":
    main()
