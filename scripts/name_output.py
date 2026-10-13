#!/usr/bin/env python3
"""产出文件命名：`<论文主标题>_<第一作者>-<形态后缀>.md`

规则（2026-10-13 立，用户要求）：
  1. **冒号降级为下划线，内容全部保留**——`:` `：` 一律替换为 `_`。
     冒号在中文标题里是**层级标记**（「主标题：子标题」），子标题往往是
     辨识度的来源，省掉会让多篇同主标题文献撞名。
  2. **破折号后的副标题省略**——`——` `—` 之后的内容不进文件名。
     副标题多为「以××为例」「之我见」这类无辨识度的套语，
     且常含引号书名号，是全文件名标点污染的主要来源。
     ⚠️ 连字符 `-` **不算**分隔符：它是形态后缀的引导符（见第 4 条），
     且标题如「STEAM-教育研究」里的 `-` 是词内连接。
  3. **清除其余标点与符号**——引号（直/弯/单双）、书名号、顿号、逗号、句号、
     问叹号、分号、括号、斜杠、省略号、空格、emoji、各类符号（•★§…）……一律删除。
     白名单按「文字类 unicode 块」枚举保留：汉字、日文假名（平/片）、韩文音节、
     西里尔（俄文等）、希腊文、注音符号，外加 ASCII 字母数字、下划线、连字符。
     ⚠️ 未列入的稀有文字（阿拉伯文、梵文、越南文带调…）仍会被删——如需支持再补白名单。
     设计要点：白名单用「保留文字」而非「删除标点」思路，避免误伤正常字母。
  4. **挂第一作者**——`_作者` 段让同一主题的多篇文献在文件夹里可区分。
  5. **形态后缀必须保留**——`-导读` / `-速览` / `-文献综述` / `-对比评述` /
     `-总报告` / `-解码卡` / `-章节卡`。这不是装饰：
     `lint_review.py` 的 `_FILENAME_MARKS` 靠这些词做**文件名兜底**的形态判定
     （内容特征判不出时才用它，但"内容判不出时兜底必须还在"有测试断言）。
     **改前缀可以，动后缀会破坏自检。**

⚠️ **英文标题的四个额外规则**（2026-10-13 用户追问后补，此前只按中文设计）：

  6. **空格转下划线，不删除**。删空格会让英文标题粘成一个词
     （`Deep Learning` → `DeepLearning`），不可读且撞名风险高。
  7. **空格包裹的连字符 ` - ` 是副标题分隔符**，与 `——` `—` 同等处理。
     英文排版的 " - " 几乎总是层级标记；而**词内连字符**（`K-12`、
     `STEAM-教育`，两侧无空白）必须保留——判据就是"两侧有无空白"。
  8. **超长标题按词边界截断**，不切半个英文单词
     （`Teachers' Professional Development` 被截成 `TeachersProfessionalDevelopmen`
     既难看又丢信息）。
  9. **中英分别设上限**：纯中文 30 字，含拉丁字母 60 字符。
     30 字符只够 4 个英文词，一个正常英文标题就被削平了。

用法：
    python3 name_output.py --title "教师教学素养：一个被忽视的研究领域" \
                           --author 夏志华 --form A
    # → 教师教学素养_一个被忽视的研究领域_夏志华-导读.md

    python3 name_output.py --title "基于“跨学科素养”的教学设计——以STEAM与“综合学习”为例" \
                           --author 钟启泉 --form A
    # → 基于跨学科素养的教学设计_钟启泉-导读.md   （破折号后副标题省略）

    python3 name_output.py --title "Computational Thinking: A Review of the Literature - Implications for K-12" \
                           --author Grover --form A
    # → Computational_Thinking_A_Review_of_the_Literature_Grover-导读.md
    #   （冒号内容保留；" - " 后副标题省略；K-12 的词内连字符不在，故整段副标题已省）

    python3 name_output.py --title "..." --author ... --form quick
    python3 name_output.py --title "..." --author ... --form A --part quick
"""
from __future__ import annotations

import argparse
import re
import sys

# ---------------------------------------------------------------- 常量

# 副标题分隔符（**其后的内容省略**）。**顺序即优先级**：长的在前，避免 `—` 抢先切掉 `——`。
# ⚠️ 词内连字符 `-`（`K-12`、`STEAM-教育`，两侧无空白）**不是**分隔符——见下面 `_SPACED_DASH`。
# ⚠️ 不含裸冒号 `:` `：`——冒号走 _COLON 替换为下划线，**内容保留**（见模块 docstring 第 1 条）。
_SUBTITLE_SEPS = ("——", "—")

# 空格包裹的破折号/连字符：英文排版里 " - " " — " 几乎总是层级标记，
# 而其两侧无空白的形态（`K-12`）是词内连接。判据就是"两侧有无空白"。
# 必须在 sanitize **之前**匹配——sanitize 会删掉空白，之后就看不出来了。
_SPACED_DASH = re.compile(r"\s+[-–—]\s+")

# 冒号 → 下划线。冒号是层级标记，其后的子标题常是辨识度来源，不能省。
_COLON = re.compile(r"[:：]")

# 空白 → 下划线。**英文标题的词间分隔必须留下**：删掉会粘成一个词
# （`Deep Learning` → `DeepLearning`），不可读且撞名风险高。
_SPACE = re.compile(r"\s+")

# 主标题长度上限。中英分开：30 字符只够 4 个英文词，正常英文标题会被削平。
_MAIN_LIMIT_CJK = 30
_MAIN_LIMIT_LATIN = 60

# 允许留在文件名里的字符。其余一律删除。
# 思路：枚举「文字类」unicode 块，而非「删标点」——后者会误伤正常字母。
# 覆盖：ASCII 字母数字 + 中日韩汉字 + 日文假名（平/片）+ 韩文音节 + 西里尔
# （俄文等）+ 希腊文 + 注音符号（台港），外加下划线（作者分隔）+ 连字符（后缀引导）。
# 仍会被删的：emoji、控制字符、各类符号（•、★、§…）、阿拉伯文/梵文等未列块。
_KEEP = re.compile(
    r"[0-9A-Za-z"
    r"\u3400-\u4dbf"   # 扩展 A 区汉字（人名生僻字）
    r"\u4e00-\u9fff"   # 中日韩统一表意文字（汉字）
    r"\u3040-\u30ff"   # 日文平假名 + 片假名（含中间长音符号）
    r"\uac00-\uD7a3"   # 韩文音节
    r"\u3130-\u318f"   # 韩文兼容 Jamo
    r"\u0400-\u04ff"   # 西里尔字母（俄文等）
    r"\u0370-\u03ff"   # 希腊文
    r"\u3100-\u312f"   # 注音符号（台港）
    r"_\-]"
)

_FALLBACK_MAIN = "未命名"
_SUFFIX = {
    "A": "导读",
    "quick": "速览",
    "B": "文献综述",
    "C": "对比评述",
    "report": "总报告",
    "card": "解码卡",
    "chapter-card": "章节卡",
}


# ---------------------------------------------------------------- 单步

def split_main_title(title: str) -> str:
    """处理标题：冒号降级为下划线（内容保留），副标题截掉。

    三步的**顺序不可交换**：
      ① 先替换冒号，② 再截裸破折号与"空格+连字符"副标题，③ 最后把空白转下划线。
    ①必须在②前：否则 `A：B——C` 会被先截成 `A`，冒号后的子标题 `B` 跟着丢。
    ②必须在③前：` - ` 靠两侧空白识别，③一删空白就认不出来了。
    """
    title = (title or "").strip()
    if not title:
        return ""
    # 书名号先剥：`《深度学习》之我见` 的主标题是 `深度学习之我见`，不是 `《深度学习》之我见`。
    # 但**不**把书名号当分隔符——它是装饰，不是层级标记。
    title = _COLON.sub("_", title)

    cut = len(title)
    for sep in _SUBTITLE_SEPS:                 # 裸 `——` `—`（中文全角连接，两侧常无空白）
        idx = title.find(sep)
        if idx != -1:
            cut = min(cut, idx)
    m = _SPACED_DASH.search(title)             # ` - ` ` — `（英文层级标记，靠空白识别）
    if m:
        cut = min(cut, m.start())

    return _SPACE.sub("_", title[:cut].strip())


def sanitize(text: str) -> str:
    """清除全部标点与空白，只留白名单字符。"""
    return "".join(ch for ch in (text or "") if _KEEP.match(ch))


def _limit_for(text: str) -> int:
    """中英分别设上限：30 字符只够 4 个英文词，正常英文标题会被削平。"""
    return _MAIN_LIMIT_LATIN if re.search(r"[A-Za-z]", text) else _MAIN_LIMIT_CJK


def _truncate(text: str) -> str:
    """按**词边界**截断，不切半个英文单词或半个中文词。

    `Teachers' Professional Development` 被硬截成 `...Developmen` 既难看又丢信息；
    优先在最后一个下划线处切开。找不到下划线（单个超长单词）才硬截。
    """
    limit = _limit_for(text)
    if len(text) <= limit:
        return text
    cut = text.rfind("_", 0, limit)
    if cut <= 0:
        cut = limit
    return text[:cut].rstrip("_")


def _tidy(text: str) -> str:
    """收尾：压缩连续下划线、去首尾下划线。

    `A：：B` 替换后成 `A__B`，作者段又自带一个 `_`，不压缩会出现
    `A___作者-导读.md` 这种三连下划线——能跑，但难看且易被误读为两层。
    """
    return re.sub(r"_+", "_", text).strip("_")


# ---------------------------------------------------------------- 主入口

def output_name(title: str, author: str = "", form: str = "A",
                part: str | None = None) -> str:
    """生成规范文件名。

    Args:
        title:  论文全题（可含副标题）。
        author: 第一作者；空则退化为不带 `_作者` 段。
        form:   形态（A/quick/B/C/report/card/chapter-card）。
        part:   形态 A 的两份产出用此区分——"quick" 出速览，其余出详版。
                 ⚠️ 与 `form` 的分工：`form` 定文档类型，`part` 只在一篇文献
                 要出两份文件时选哪一份。

    Returns:
        形如 `基于跨学科素养的教学设计_钟启泉-导读.md` 的文件名。
    """
    if form not in _SUFFIX:
        raise ValueError(
            f"未知形态 {form!r}；可用：{', '.join(sorted(_SUFFIX))}"
        )

    suffix = _SUFFIX["quick" if part == "quick" else form]

    main = _tidy(_truncate(sanitize(split_main_title(title)))) or _FALLBACK_MAIN
    who = _tidy(sanitize(author))
    stem = f"{main}_{who}" if who else main
    return f"{stem}-{suffix}.md"


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="按 <论文主标题>_<第一作者>-<形态后缀>.md 规则生成产出文件名",
    )
    p.add_argument("--title", required=True, help="论文全题（可含副标题）")
    p.add_argument("--author", default="", help="第一作者（可省）")
    p.add_argument("--form", default="A", choices=sorted(_SUFFIX),
                   help="形态，默认 A")
    p.add_argument("--part", default="", choices=["", "quick"],
                   help="形态 A 出速览时传 quick")
    a = p.parse_args(argv)

    try:
        print(output_name(a.title, a.author, a.form, a.part or None))
    except ValueError as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
