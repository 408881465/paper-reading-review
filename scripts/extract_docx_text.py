#!/usr/bin/env python3
"""Word (.docx) → 带结构的纯文本。**仅用标准库**，无需安装任何依赖。

## 为什么需要这个脚本

形态 D 的输入里有相当比例是 `.docx`（政策文件、课程指南、素养框架这类
由机构发布的文本）。没有抽取器时 `batch_extract.py` 只能把这类文件标成
「待其它提取器」——**链路到此为止**；而政策文件恰恰是申报书「国内外研究现状」
与「问题的提出」要引的关键依据（2026-10-08 实测：本课题语料里 4 份政策文件
全是 .docx，其中教育部通知、通识指南、上海素养框架均为必引）。

## 必须做对的三件事（都是从真实语料里查出来的）

1. **跳过 `w:instrText`（域指令）。** Word 目录域内含 TOC／HYPERLINK／PAGEREF
   指令；当正文抽出会得到 `TOC \\o "1-3"`、`PAGEREF _Toc235530059` 这类垃圾
   （实测某文件泄漏 17 处 PAGEREF + 17 处 HYPERLINK）。
2. **解码 XML 实体。** 直接按字符串读 `document.xml` 会漏出 `&quot;`、`&gt;`
   （实测某文件 **54 处 `&quot;`**）。本脚本用 `xml.etree` 解析，天然解码。
3. **目录域的"结果"也要剥掉**（除非 `--keep-toc`）。目录域由
   `begin → instrText → separate → 结果文字 → end` 组成；只跳 `instrText`
   仍会把整份目录当正文留下（实测某文件多出 17 行 `一、…\t- 2 -`），
   而其中的 `- 2 -` 正是 §5.1 警告过的**不可靠页码**。

## 标题判定：只认结构，不做文字猜测（除非显式开启）

优先级：`w:outlineLvl`（Word 大纲级别的唯一权威来源）→ 显式标题样式名
（`Heading1`／`标题1`／`head1`／`Heading 1`）。

**刻意不把 `pStyle` 的裸数字（`"1"`/`"2"`/`"3"`）当标题级别**——
中文 Word 模板里这类样式 ID 常是正文变体。实测教训：某政策文件 70 段的
`pStyle` 全是 `"3"` 且无 `outlineLvl`，若把 `"3"` 当三级标题，
**整篇正文会被标成 70 个 `###`**，伪造出文档并不存在的结构。
这类文件请用 `--heading-from-numbering`（显式开启的中文序号启发式）。

## 回指基准：**章节名，不是页码**

`.docx` 没有固定页码——分页随版式重排，页脚 PAGE 域算出的页码不可作引用依据。
故本脚本**不产出 `===== PAGE n =====` 标记**；引用请按
`references/batch-workflow.md` §5.1 第 2 条**回指章节名**
（标题已保留为 Markdown 标题；无标题结构时，章节号仍在正文里，可直接检索）。
需要更细粒度时用 `--para-numbers`，每段前加 `[¶N]`。

## 用法

    python3 scripts/extract_docx_text.py <x.docx> -o <out.txt> \
        [--keep-toc] [--para-numbers] [--heading-from-numbering]

退出码：0 正常；1 打不开／不是 docx／未取到正文。
"""

import argparse
import os
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
DOCUMENT = "word/document.xml"

# 显式标题样式名（**不含**裸数字，理由见文件头）
_HEADING_STYLE = re.compile(r"^(?:heading|head|标题)\s*(\d)$", re.IGNORECASE)

# 中文政策/学术文本的常见章节序号，仅供 --heading-from-numbering 使用
_NUMBERING = [
    (1, re.compile(r"^第[一二三四五六七八九十]+[章部分]")),
    (2, re.compile(r"^[一二三四五六七八九十]+、")),
    (3, re.compile(r"^（[一二三四五六七八九十]+）")),
    (4, re.compile(r"^\d+(?:\.\d+)*[、.．]?\s*\S")),
]


def _is_docx(path: str) -> bool:
    try:
        with zipfile.ZipFile(path) as z:
            return DOCUMENT in z.namelist()
    except (zipfile.BadZipFile, OSError):
        return False


def _style_heading_level(p) -> int:
    """样式名给出的标题级别；非显式标题样式返回 0。"""
    ppr = p.find(W + "pPr")
    if ppr is None:
        return 0
    style = ppr.find(W + "pStyle")
    if style is None:
        return 0
    sid = (style.get(W + "val") or "").strip()
    m = _HEADING_STYLE.match(sid)
    return int(m.group(1)) if m else 0


def _outline_level(p) -> int:
    """段落的大纲级别（1 起）；取不到返回 0（＝正文）。"""
    ppr = p.find(W + "pPr")
    if ppr is not None:
        lvl = ppr.find(W + "outlineLvl")
        if lvl is not None:
            try:
                return int(lvl.get(W + "val", "9")) + 1
            except ValueError:
                pass
    return _style_heading_level(p)


def _numbered_level(text: str) -> int:
    for level, pat in _NUMBERING:
        if pat.match(text):
            return level
    return 0


def _paragraph_text(p, state, drop_toc: bool):
    """段落的可见文字；同时推进域（field）状态机。

    **逐个子元素按文档序处理**：`w:t` 取文字、`w:instrText` 取域指令（不取文字）、
    `w:fldChar` 驱动深度计数。目录域跨多个段落，故状态由调用方持有。
    """
    parts = []
    skip_this = False
    for node in p.iter():
        tag = node.tag
        if tag == W + "fldChar":
            kind = node.get(W + "fldCharType")
            if kind == "begin":
                state["depth"] += 1
            elif kind == "end":
                if state["toc_depth"] == state["depth"]:
                    state["toc_depth"] = None
                state["depth"] = max(0, state["depth"] - 1)
        elif tag == W + "instrText":
            instr = (node.text or "").strip()
            if state["toc_depth"] is None and instr.upper().startswith("TOC"):
                state["toc_depth"] = max(state["depth"], 1)
        elif tag == W + "t":
            if drop_toc and state["toc_depth"] is not None:
                skip_this = True
            else:
                parts.append(node.text or "")
        elif tag == W + "tab":
            parts.append("\t")
        elif tag in (W + "br", W + "cr"):
            parts.append("\n")
    del skip_this
    return "".join(parts).strip()


def extract(path: str, with_para_numbers: bool = False,
            keep_toc: bool = False, heading_from_numbering: bool = False):
    """→ (行列表, 段落数, 目录域是否被剥掉)。"""
    if not _is_docx(path):
        raise ValueError(f"不是有效的 .docx（缺 {DOCUMENT}）：{path}")
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read(DOCUMENT))

    state = {"depth": 0, "toc_depth": None}
    lines, n_para, toc_dropped = [], 0, False
    for p in root.iter(W + "p"):
        text = _paragraph_text(p, state, drop_toc=not keep_toc)
        if not text:
            if state["toc_depth"] is None and not toc_dropped:
                continue
            continue
        n_para += 1
        lvl = _outline_level(p)
        if lvl < 1 and heading_from_numbering:
            lvl = _numbered_level(text)
        prefix = f"[¶{n_para}] " if with_para_numbers else ""
        if 1 <= lvl <= 6:
            lines.append(f"{'#' * lvl} {prefix}{text}")
        else:
            lines.append(f"{prefix}{text}")
    return lines, n_para, toc_dropped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Word (.docx) → 带结构的纯文本（仅标准库）")
    ap.add_argument("docx", help="输入 .docx")
    ap.add_argument("-o", "--output", help="输出 txt（默认与输入同名 .txt）")
    ap.add_argument("--keep-toc", action="store_true",
                    help="保留目录域的渲染结果（默认剥掉：它是导航噪声，且其中的页码不可靠）")
    ap.add_argument("--para-numbers", action="store_true",
                    help="每段前加 [¶N]，便于细粒度回指")
    ap.add_argument("--heading-from-numbering", action="store_true",
                    help="对无大纲级别的文档，按中文序号（一、／（一）／1.）猜标题级别。"
                         "**默认关闭**：中文 Word 模板的裸数字样式常是正文，猜错会伪造文档结构")
    args = ap.parse_args(argv)

    if not os.path.exists(args.docx):
        print(f"打不开：{args.docx}", file=sys.stderr)
        return 1
    try:
        lines, n_para, toc = extract(args.docx, args.para_numbers,
                                     args.keep_toc, args.heading_from_numbering)
    except (ValueError, ET.ParseError, OSError) as exc:
        print(f"解析失败：{exc}", file=sys.stderr)
        return 1
    if not n_para:
        print("未取到任何段落——该 docx 可能只有图形，或正文在文本框内", file=sys.stderr)
        return 1

    out = args.output or os.path.splitext(args.docx)[0] + ".txt"
    header = [
        f"<!-- 来源：{os.path.basename(args.docx)}（.docx，无固定页码）",
        "     回指基准：**章节名**（见下方 Markdown 标题）。docx 分页随版式重排，",
        "     页脚 PAGE 域算出的页码不可作引用依据，故本文件不含 PAGE 标记。",
        f"     段落数：{n_para}。域指令与 XML 实体已处理"
        + ("；目录域渲染结果已剥掉（--keep-toc 可保留）。 -->" if toc else "。 -->"),
        "",
    ]
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(header + lines) + "\n")
    print(f"已抽取 {n_para} 段 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
