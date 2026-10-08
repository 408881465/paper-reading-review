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
from collections import Counter

MARKER = "===== PAGE {} ====="

# 学术 PDF 常见噪声：页眉页脚、水印。
# ⚠️ 不要把裸 URL 当噪声删掉——参考文献里的 DOI/链接是回查原文的证据，
# 删掉就违反了本技能「证据可回指、不编造」的核心纪律。
_HEADER_NOISE = re.compile(
    r"^\s*(第\s*\d+\s*页|Page\s+\d+|©.*|All\s+rights\s+reserved.*|"
    r"Downloaded\s+from.*)\s*$",
    re.IGNORECASE,
)
# 只在「连字符前面是字母」时接回断词。
# 旧写法 (\w)-\n(\w) 会把数字区间也接起来：「pp. 12-\n15」→「pp. 1215」、
# 「2019-\n2020」→「20192020」，直接毁掉回指用的页码与年份。
_HYPHEN_BREAK = re.compile(r"([^\W\d_])-\n(\w)", re.UNICODE)
_MULTI_BLANK = re.compile(r"\n{3,}")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")
# 单独成行的页码/裸数字
_LONE_DIGITS = re.compile(r"^\s*\d{1,4}\s*$")

# 一页提取出的非空白字符少于这个数，基本可判定该页没有文本层（扫描件）。
# 取值要低：表格页、参考文献页天然很短；真正的扫描页是 0 字符。
# 「这一页是不是没有文本层」的判据阈值（去空白后的字符数）。
# ★这不是局部常量：batch_extract.py 复用本模块时也读它（extractor.SCANNED_PAGE_MIN_CHARS），
#   故保持公开名。调它会同时影响单篇抽取与批量抽取的「需OCR」判定。
SCANNED_PAGE_MIN_CHARS = 10


def _split_lines(raw: str):
    return raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")


def detect_page_number_lines(pages_raw, min_ratio=0.5):
    """识别哪些「裸数字行」真的是页码，返回 {(页序号, 位置)} 集合。

    旧实现把所有 1–4 位的裸数字行一律删掉。这在实证论文里是灾难：
    表格里的样本量、年份、频数都可能是独立成行的裸数字，删掉就等于
    把「证据可回指」的原料扔了。

    这里的判据：页码总是落在页首或页尾，且与 PDF 页序保持恒定偏移。
    只有「位置在首/尾」且「满足一个在多数页上都成立的偏移」的裸数字才删。
    """
    candidates = []
    for idx, raw in enumerate(pages_raw):
        lines = _split_lines(raw)
        filled = [i for i, line in enumerate(lines) if line.strip()]
        if not filled:
            continue
        for pos, line_no in ((0, filled[0]), (1, filled[-1])):
            stripped = lines[line_no].strip()
            if _LONE_DIGITS.match(stripped):
                candidates.append((idx, pos, int(stripped)))

    if not candidates:
        return set()

    offsets = Counter(value - (idx + 1) for idx, _pos, value in candidates)
    offset, hits = offsets.most_common(1)[0]
    if hits < max(2, min_ratio * len(pages_raw)):
        return set()
    return {(idx, pos) for idx, pos, value in candidates if value - (idx + 1) == offset}


def clean_page_text(raw: str, drop_positions=()) -> str:
    """清洗单页文本：去页眉页脚、接回断词、压缩空白。

    保留段落结构（换行），只压缩连续空行和行内多余空格——段落边界是
    阅读密码定位句子功能的依据，不能压平。

    drop_positions：由 detect_page_number_lines 判定的真页码位置
    （0=首个非空行，1=末个非空行）。默认不删任何裸数字。
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
        lines.append(line)

    # 页码只在首/尾各判一次，且必须按「未删行」的位置对齐，
    # 所以先标记再统一删除，避免边删边错位。
    filled = [i for i, line in enumerate(lines) if line.strip()]
    to_drop = set()
    for pos, line_no in ((0, filled[0] if filled else None),
                         (1, filled[-1] if filled else None)):
        if pos in drop_positions and line_no is not None:
            to_drop.add(line_no)
    lines = [line for i, line in enumerate(lines) if i not in to_drop]

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
        doc.close()
        return 1

    try:
        pages_raw = [doc[page_no - 1].get_text()
                     for page_no in range(start, end + 1)]
    finally:
        doc.close()

    if args.pages_only:
        print(f"总页数: {total}  提取范围: {start}-{end}")
        return 0

    page_number_lines = detect_page_number_lines(pages_raw)
    chunks, empty_pages = [], []
    for offset, raw in enumerate(pages_raw):
        drop_positions = {pos for idx, pos in page_number_lines if idx == offset}
        cleaned = clean_page_text(raw, drop_positions=drop_positions)
        if len(re.sub(r"\s", "", cleaned)) < SCANNED_PAGE_MIN_CHARS:
            empty_pages.append(start + offset)
        chunks.append(f"{MARKER.format(start + offset)}\n{cleaned}\n")

    output = "".join(chunks)

    # 扫描件没有文本层，get_text() 返回空串。旧版会静默产出只有 PAGE 标记的
    # 空文件（exit 0），下游「读到空文件」极易变成凭印象编造内容——
    # 这正是本技能「不编造、可回指」纪律最怕的失败模式。必须显式报警。
    # 只有「多数页都提不出文本」才判定为扫描件。少数几页可能是插图页或
    # 空白页，逐页报警会变成噪声——真正危险的是整篇都没有文本层。
    scanned = bool(empty_pages) and len(empty_pages) >= max(
        1, 0.5 * len(pages_raw))
    if scanned:
        preview = "、".join(str(p) for p in empty_pages[:10])
        more = " 等" if len(empty_pages) > 10 else ""
        print(
            f"⚠️  {len(empty_pages)}/{len(pages_raw)} 页几乎提取不到文本"
            f"（第 {preview}{more} 页）。该 PDF 很可能是扫描件或纯图片版式，"
            "没有文本层。请先做 OCR 再解读，不要直接把空文本当成「原文没有写」。",
            file=sys.stderr,
        )

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(output)
        chars = len(output)
        print(f"已写入 {args.output}（{total} 页中的 {start}-{end}，{chars} 字符）")
    else:
        sys.stdout.write(output)
    return 2 if scanned else 0


if __name__ == "__main__":
    sys.exit(main())
