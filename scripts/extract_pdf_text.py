#!/usr/bin/env python3
"""从 PDF 提取带页码标记的纯文本，供阅读密码标注使用。

背景：多数论文 PDF 是扫描件或版面复杂的双栏排版，直接读会得到乱序文本。
本脚本把文本按页提取并加统一标记，再针对学术论文常见的版面噪声做清洗。

用法：
    python3 extract_pdf_text.py <pdf路径> [-o 输出路径] [--start 1] [--end 20]

输出格式（PAGE 标记便于回引原句位置）：
    ===== PAGE 1 =====
    <该页文本>

依赖：PyMuPDF (fitz)。缺失时给出明确安装指引后退出。
"""

import argparse
import re
import sys

MARKER = "===== PAGE {} ====="

# 学术 PDF 常见噪声：页眉页脚、页码、连字符断词、水印
_HEADER_NOISE = re.compile(
    r"^\s*(第\s*\d+\s*页|Page\s+\d+|©.*|All\s+rights\s+reserved.*|"
    r"Downloaded\s+from.*|https?://\S+)\s*$",
    re.IGNORECASE,
)
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_MULTI_BLANK = re.compile(r"\n{3,}")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
# 单独成行的页码/裸数字
_LONE_DIGITS = re.compile(r"^\s*\d{1,4}\s*$")


def clean_page_text(raw: str) -> str:
    """清洗单页文本：去页眉页脚、接回断词、压缩空白。

    保留段落结构（换行），只压缩连续空行和行内多余空格——段落边界是
    阅读密码定位句子功能的依据，不能压平。
    """
    text = raw.replace("\r\n", "\n").replace("\r", "\n")

    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            lines.append("")
            continue
        if _HEADER_NOISE.match(stripped):
            continue
        if _LONE_DIGITS.match(stripped):
            continue
        lines.append(line)

    text = "\n".join(lines)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)      # 接回跨行断词
    text = _MULTI_SPACE.sub(" ", text)            # 行内多空格
    text = _MULTI_BLANK.sub("\n\n", text)        # 压缩连续空行
    return text.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="提取 PDF 文本并加页码标记")
    parser.add_argument("pdf", help="PDF 文件路径")
    parser.add_argument("-o", "--output", help="输出 txt 路径（默认打印到 stdout）")
    parser.add_argument("--start", type=int, default=1, help="起始页（1-based）")
    parser.add_argument("--end", type=int, default=0, help="结束页，0 表示到末页")
    parser.add_argument("--pages-only", action="store_true", help="只输出页码统计")
    args = parser.parse_args()

    try:
        # PyMuPDF ≥1.24 推荐 pymupdf 模块名；旧版只有 fitz
        try:
            import pymupdf as fitz
        except ImportError:
            import fitz
    except ImportError:
        print(
            "缺少 PyMuPDF。请先安装：\n"
            "  pip install pymupdf\n"
            "或用带依赖的解释器运行本脚本。",
            file=sys.stderr,
        )
        return 1

    try:
        doc = fitz.open(args.pdf)
    except Exception as exc:  # 损坏/加密/非 PDF
        print(f"无法打开 PDF：{exc}", file=sys.stderr)
        return 1

    total = doc.page_count
    start = max(1, args.start)
    end = min(args.end or total, total)

    if start > total:
        print(f"起始页 {start} 超出文档页数 {total}", file=sys.stderr)
        return 1

    chunks = []
    for page_no in range(start, end + 1):
        raw = doc[page_no - 1].get_text()
        cleaned = clean_page_text(raw)
        chunks.append(f"{MARKER.format(page_no)}\n{cleaned}\n")

    if args.pages_only:
        print(f"总页数: {total}  提取范围: {start}-{end}")
        return 0

    output = "".join(chunks)
    doc.close()

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(output)
        chars = len(output)
        print(f"已写入 {args.output}（{total} 页中的 {start}-{end}，{chars} 字符）")
    else:
        sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
