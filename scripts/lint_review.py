#!/usr/bin/env python3
"""review 文档自检：把 SKILL.md 里「靠人记住」的几条纪律变成可执行检查。

SKILL.md 要求成稿前逐条走 15 项自检，但其中至少六项是**机械可查的**：
缩写是否残留、字数是否落在区间、空白的理论依据有没有配、"洗衣店接衣单"是否
复发、证据能否回指、一问一答是否被合并。人工搜 grep 会漏，尤其在一份 8000 字
的详版里。

用法：
    python3 lint_review.py 04_单篇导读/xxx-导读.md
    python3 lint_review.py 05_主题综述/*.md --json
    python3 lint_review.py xxx.md --form A --min 1500 --max 10000 --strict

形态（--form）：
    A    单篇详版（默认区间 1500–10000 字）
    quick 一页速览（300–1800 字）
    B    多篇主题综述（800–20000 字）
    C    对比评述（800–15000 字）
    card 解码卡·T2（200–2500 字）
    chapter-card 章节级解码卡（400–5000 字；>60 页的专著/学位论文按章解码，
                 文件名含「章节」或路径含「专著章节」时自动判定）
    report 总报告（不限字数）
    doc   规范文档/模板（references/ 与 assets/，只查缩写残留，其余规则不适用）
    auto 按文件名/结构猜（路径含 references/ 或 assets/ 一律判为 doc）

单条规则可用 --skip 关闭：abbr / chars / banned / gap-rat / laundry / locator / codes / merged / limits。
（模板与说明文档**必须**引用禁用词与密码缩写来讲规则，对它们套 review 规则只会满屏假警报——
假警报会让人开始忽略 lint，比没有 lint 更糟。`doc` 形态就是为此设的。）

退出码：0 通过（可含 warning）；1 有 error（阻断出稿）。
"""

import argparse
import json
import re
import sys

# ---------------------------------------------------------------- 规则常量

CODE_NAMES = ["作者提出的主要问题", "现有文献综述", "现有文献批评", "空白", "理论依据",
              "研究结果", "一致的研究发现", "相反的研究发现", "作者给出的答案",
              "未来研究建议", "批评点", "明显的遗漏点", "待探讨的相关问题", "能否理顺"]
CODE_ABBR = ["WTD", "SPL", "CPL", "GAP", "RAT", "ROF", "RCL", "RTC", "WTDD",
             "RFW", "POC", "MOP", "RPP", "WIL"]

# 被 SKILL.md 点名的空话／越界表述。它们之所以必须拦，是因为写出来读者
# 无法核对——而本技能的全部价值就在「可回指」。
BANNED = [
    ("有一定参考价值", "判定口径必须是四值：纳入（核心）/纳入（背景）/暂不纳入/待核查"),
    ("有一定借鉴意义", "空泛评价，改为具体判据"),
    ("方法有待加强", "批评点必须落在测度效度/样本代表性/因果越界等具体处"),
    ("方法有待改进", "批评点必须落在具体处"),
    ("建议进一步查阅", "检索建议属 cnki-skills / global-biblio-base 的边界"),
    ("建议未来进一步", "检索建议属其它技能边界"),
    ("有待进一步研究", "空话；要写就必须写成可检验的具体问题"),
    ("具有重要的理论意义和现实意义", "无信息量，删"),
]

FORM_RANGES = {
    "A": (1500, 10000), "quick": (300, 1800), "B": (800, 20000),
    "C": (800, 15000),
    # 一页卡上限 2500 → **3200**（2026-10-09 按真实产出标定放宽）。
    # 依据：课题 78 份真实解码卡的字数分布为 min 1222／P25 1726／中位 2125／
    # P75 2416／**P90 2799**／max 3088，而 2500 恰好切在 P75 与 P90 之间——
    # **19%（14/73）的正常产出触发**。更关键的是核对发现**其中 13 份已在文末
    # 按规则要求标注了字数与取舍顺序**，即"规则给的出路被遵守了"，说明这不是
    # 产出习惯问题，而是上限与实际标定不符。
    # 取 3200：覆盖实测最大值（3088），只对**真正离群**的卡报警。
    # ★它仍是 WARN 而非 ERROR，不阻断出稿。
    "card": (200, 3200),
    # 章节级解码卡面对的是 100–300 页的专著/学位论文，普通 T2 卡的上限套不过来。
    # 实测：不给它单独的规格，队友就会「把 T2 当小型详版写」或「把 200 页书压成一张卡」，
    # 两种做法都让档位分层失效（独立校验 2026-10-07 报出 15 张卡超限）。
    "chapter-card": (400, 5000),
    # 总报告：形态 D 的收口产出，要交代整批的覆盖、主题、空白与三查结果。
    # 原来给 (0, 10**9) 等于"随便多短都算合格"——而它恰恰是**最不能敷衍**的一份
    # （第 1 节的精读声明、第 5 节的三查结论都是必填）。故设 1200 字下限；
    # 上限不设：批次越大它越该长。
    "report": (1200, 10 ** 9),
    "doc": (0, 10 ** 9),
}

# 「规范文档」形态（references/ 与 assets/ 下的说明与模板）：它们的工作就是
# **引用**禁用词与密码缩写来说明规则，逐条套用 review 规则只会满屏假警报。
# 假警报的代价很实在——人一旦开始忽略 lint 输出，它就等于不存在。
DOC_FORM_SKIP = {"chars", "banned", "gap-rat", "laundry", "locator",
                 "codes", "merged", "limits"}

ALL_RULES = ("abbr", "chars", "banned", "gap-rat", "laundry", "locator",
             "codes", "merged", "limits", "hygiene")

# 一页速览与解码卡是压缩件，不必十码齐备；详版与综述要求齐备
FORMS_REQUIRING_ALL_CODES = {"A"}

_CJK = re.compile(r"[\u4e00-\u9fff]")
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s*(.+?)\s*$", re.MULTILINE)
_PARA = re.compile(r"\n\s*\n")
# 不能用 \b：Python 的 \w 包含中文，缩写紧贴汉字时（如「ROF研究」）\b 不成立，
# 反而漏检。改成显式的「前后不是 ASCII 字母」判据。
_ABBR = re.compile(r"(?<![A-Za-z])(" + "|".join(CODE_ABBR) + r")(?![A-Za-z])")
_FENCE = re.compile(r"^```", re.MULTILINE)
# 「张三（2020）」这类以作者开头的段落 = 洗衣店接衣单的典型句式
_AUTHOR_LEAD = re.compile(r"^\s*[\u4e00-\u9fff]{2,4}(?:等)?\s*[（(]\s*\d{4}\s*[）)]")
_YEAR_CITE = re.compile(r"[（(]\s*\d{4}[a-z]?\s*[）)]")
# 可回指标记。除页码外，还必须认「章节名回指」——batch-workflow.md §5.1 第 2 条
# 明确规定：**没有页码标注时就回指章节名**（页码被抽坏、或学位论文前段本无页码时）。
# 旧实现只认 p.N / 第N页节，于是**按 §5.1 合规写的稿子会被警告"可回指 0 处"**
# （2026-10-08 用三篇真实文献实测：L0041 页码抽坏、L0074 无页码，只能章节名回指）。
_LOCATOR = re.compile(
    r"(?:p\.\s*\d+"                       # p.14
    r"|第\s*[0-9]+\s*[页节]"                  # 第 3 页 / 第 2 节
    r"|第\s*[一二三四五六七八九十]+\s*节"           # 第 二 节（中文数字）
    r"|[「“\"][^」”\"]{2,24}[」”\"]\s*(?:一节|节|部分)"  # 「研究背景」一节
    r"|（\s*第\s*[0-9]+\s*页\s*）)"
)
_MERGED = re.compile(r"(?:研究|分析|探讨)了?.{2,40}(?:并|且|同时)发现")
_PRAISE = re.compile(r"本文评述")

# 「局限」一词若紧跟这些标记，说明作者已把归属写清，不必再要求「本文评述」。
# 覆盖两种情形：① 说作者**没有**自陈（未自陈/未提出/作者未…）；
#               ② 说作者**自陈了**（作者自陈/作者指出的局限）。
_LIMIT_ATTR = ("本文评述", "评述认为", "作者自陈", "自陈", "作者未", "未提出",
               "作者指出的", "作者承认", "作者提到")

# 「空白必配理论依据」里「理论依据」的**规范写法**。SKILL.md 第 3 步把两者并列：
# 「每组空白后必写理论依据（因此可开展的研究是……）」，故二者都算已配。
GAP_RATIONALE_MARKERS = ("理论依据", "因此可开展的研究是")

# 出现这些标记，说明文中的禁用词是**被引用**（讲规则、举例、给反例），不是违规。
_BAN_MENTION = ("禁止", "不写", "不要写", "避免", "勿", "不得", "严禁", "不应",
                "不能写", "忌", "杜绝", "禁用")


def _mentions_only(text: str, word: str, window: int = 12) -> bool:
    """word 在文中**只被提及**（前 window 字内有劝阻标记）即视为未使用。

    为什么要做这个区分：模板与规则文档必须**引用**禁用词才能讲清规则
    （例如「禁止『方法有待加强』」）。若不区分使用与提及，这些文档会被
    满屏假警报淹没——而假警报会让人开始忽略 lint，比没有 lint 更糟
    （本文件开头的 DOC_FORM_SKIP 注释已就同一问题表过态）。
    """
    start = 0
    while True:
        i = text.find(word, start)
        if i < 0:
            return True                      # 一次都没真正使用
        if not any(k in text[max(0, i - window):i] for k in _BAN_MENTION):
            return False                     # 找到一处未被劝阻标记覆盖的使用
        start = i + len(word)


def _has_unattributed_limit(text: str) -> bool:
    """是否存在**归属不明**的「局限」——即该词前 12 字内没有任何归属标记。

    为什么要这样判：一句话里出现「局限」并不等于违规。真正的违规是
    **分不清这是作者自陈的局限，还是本文评述发现的局限**。已写明归属的句子
    （如「作者未自陈局限」）本身就是合规写法，报它等于制造假警报。
    """
    for m in re.finditer("局限", text):
        window = text[max(0, m.start() - 12):m.start()]
        if not any(k in window for k in _LIMIT_ATTR):
            return True
    return False


# ---- hygiene：脚本产物污染（2026-10-08 实战，两类都会静默混进正文） ----
# ① HTML 实体：`&#x7684;`（＝「的」）。某些库/工具把非 ASCII 转义后未回转。
#    正文里出现即为污染，无正当用途。
# 数字实体（&#x7684; / &#34;）**与命名实体（&quot; / &amp; / &nbsp;）都要认**。
# 只认前者会漏掉后者——而真实语料里恰恰有：某政策文件 .docx 的旧版抽取结果里
# 有 54 处 `&quot;`（2026-10-08 查出）。命名实体清单取常用的那一批，
# 不用宽泛的 `&\w+;` 以免误伤正文里的 `&` 与 `A;B` 形式。
_HTML_ENTITY = re.compile(
    r"&#x?[0-9A-Fa-f]{2,6};"
    r"|&(?:quot|amp|lt|gt|nbsp|apos|ldquo|rdquo|lsquo|rsquo|mdash|ndash|hellip|middot);")
# ② 反斜杠引用字面泄漏：`re.sub` 的替换体写成 r"\1" + text（而非函数）时，
#    `\1` 会被当字面量输出。特征：`\数字` 紧贴汉字，或独占行首/行尾。
#    正则讨论通常写在行内代码里，故检查前先剥掉代码块与行内代码，避免假警报
#    （假警报的代价见 DOC_FORM_SKIP 处说明：人一旦忽略 lint 输出，它就等于不存在）。
#    ★前后一律用显式 ASCII 判据，**不能用 \w 或 \b**——Python 的 \w 包含中文，
#    `\1协同` 这种「紧贴汉字」的泄漏恰恰是最典型的形态，用 \w 会整类漏掉。
_BACKREF_LEAK = re.compile(r"(?<![A-Za-z0-9_\\])\\[1-9](?![A-Za-z0-9_])")

# 章节级卡的**内容特征**。为什么不能只看文件名（2026-10-08 用真实产出实测）：
# 同一份 3417 字的内容，文件名带「章节」→ 判 chapter-card（上限 5000）**通过**；
# 把文件名里的「章节」去掉 → 判 card（上限 2500），**报超限 917 字**。
# card 与 chapter-card 的上限差一倍，误判的代价很实在；而产出命名是自由的，
# 技能文档此前也从没要求章节卡必须叫「章节解码卡」。
# 取真实章节卡的 H1 用词：抽查 20 份一页卡，含这两个特征的 **0 份**（无假阳性）。
_CHAPTER_CARD_MARK = ("章节级解码卡", "本卡所解的章")

# ══════════════════════════════════════════════════════════════════════════════
# 形态判定：**内容特征优先，文件名退居兜底**
#
# 为什么根治（2026-10-08/09 累计实测）：`guess_form` 是整套 lint 的**入口**——
# 形态判错，后面所有字数区间、必填栏、禁用词全部按**错的规格**检查。
# 而旧实现**以文件名为第一判据**，内容兜底是逐个后加的例外。同一类缺陷出现了三次：
#   ① 章节卡：文件名不含「章节」→ 判成一页卡，3417 字被报"超限 917 字"
#   ② 一页卡：文件名不含「解码卡」→ 判成详版，1017/1129/1392 字被报"低于 1500 下限"
#   ③ 总报告：文件名不含「总报告」→ 判成 B，字数区间被换成 800–20000（**检查被放松**）
# 三次都是打补丁。根治的做法是**把内容特征提到文件名之前**。
#
# 签名取自六套模板自身的**专有小节名**（探针实测：6 套模板各只命中自己；
# 16 份真实产物中 12 份与旧判定一致、4 份仅是标签写法差异，实为同一形态）。
# `need` 是"至少命中几处才算"——对易被正文顺口提及的词（对比矩阵/综述摘要）
# 要求 2 处，避免详版里提一句就被改判。
# ⚠️ **顺序即优先级**：`chapter-card` 必须在 `card` 之前——章节卡正文同时含
#    `## 0. 题录` 等卡特征（章节卡模板本身就带这些节）。
_FORM_SIGNATURES = (
    ("chapter-card", _CHAPTER_CARD_MARK, 1),
    ("card", ("## 0. 题录", "## 1. 结构性密码", "## 2. 策略性密码",
              "## 3. 与本课题的接口"), 2),
    ("report", ("覆盖与精读声明", "一致性三查结果", "# 总报告"), 1),
    ("quick", ("一句话结论", "三条要点", "一个批评点"), 2),
    ("C", ("对比矩阵", "共识与分歧", "对比综述"), 2),
    ("B", ("综述摘要", "文献范围与筛选说明", "现有文献的主题格局"), 2),
    ("A", ("导读摘要", "研究定位", "读后总评"), 1),
)

# 文件名兜底（内容判不出时才用）。**顺序即优先级**。
_FILENAME_MARKS = (
    ("chapter-card", ("章节", "卡")),
    ("card", ("解码卡",)),
    ("report", ("总报告",)),
    ("quick", ("速览",)),
    ("C", ("对比", "评述")),
    ("B", ("综述",)),
    ("A", ("导读",)),
)


def _hit_filename(name: str, marks):
    if marks == ("章节", "卡"):        # 需同时含「章节」与「卡」——
        return "章节" in name and ("卡" in name or "解码" in name)
    return any(m in name for m in marks)

# 一页卡（`card`）的**正文特征**——两套模板的小节编号体系不同，可靠区分：
#   一页卡模板：`## 0. 题录` / `## 1. 结构性密码` / `## 2. 策略性密码` / `## 3. 与本课题的接口`（阿拉伯数字）
#   详版模板：  `## 一、导读摘要` / `## 二、研究定位` …（中文数字）
# 为什么必须有内容兜底（2026-10-09 用形态 D 端到端跑批时查出）：
#   产出命名是自由的，按 `T2-L0110-作者-主题.md` 这样命名很自然，**文件名不含"解码卡"**；
#   而卡正文含「作者提出的主要问题」，于是落到下面的结构兜底被判成 `A` ——
#   一页卡被套上详版的 **1500 字下限**，三张卡（1017／1129／1392 字）全部报"低于下限"。
_CARD_MARK = ("## 0. 题录", "## 1. 结构性密码", "## 2. 策略性密码", "## 3. 与本课题的接口")

# 总报告（`report`）的**正文特征**。★这是**第三处**"形态判定靠文件名"的补丁
# （前两处：章节卡、一页卡）。2026-10-09 实测：把总报告按自由命名后判成 `B`，
# 于是套上 B 的 800–20000 区间，而非 report 的 1200–∞——**检查被放松**。
# 总报告模板有两处别处不会出现的节名，可作稳定特征。
_REPORT_MARK = ("覆盖与精读声明", "一致性三查结果", "# 总报告")
_FENCE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")


def strip_code(text: str) -> str:
    """剥掉围栏代码块与行内代码，只留正文——用于「污染」类检查。"""
    return _INLINE_CODE.sub("", _FENCE_BLOCK.sub("", text))


_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)


def strip_comments(text: str) -> str:
    """剥掉 HTML 注释——**注释不是正文**（渲染后不出现），不该参与内容校验。

    为什么要单独做：模板文件通篇是 `<!-- 填写说明 -->`，形态校验若把注释当正文，
    就会拿"给填写者的指引"去判"成稿是否合规"（2026-10-08 实测：单篇模板的
    两处裸缩写 ERROR 全部来自注释里的 `WTD = …` 定义清单）。
    """
    return _COMMENT.sub("", text)



# ---------------------------------------------------------------- 工具

def count_cjk(text: str) -> int:
    """按中文字符数计字数。中英混排时应统计「中文 + 英文单词」，
    只数字符会把英文摘要类文献的长篇误判为过短。"""
    words = len(re.findall(r"[A-Za-z]+", text))
    return len(_CJK.findall(text)) + words


def split_sections(text: str):
    """→ [(标题, 正文)]，正文不含标题行。无标题时返回 [("", 全文)]。"""
    marks = list(_HEADING.finditer(text))
    if not marks:
        return [("", text)]
    sections = []
    if marks[0].start() > 0:
        sections.append(("", text[:marks[0].start()]))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        sections.append((m.group(2), text[m.end():end]))
    return sections


def split_sections_with_levels(text: str):
    """→ [(层级, 标题, 正文)]。`split_sections` 丢掉了层级，而"本节与下一节"
    这种近似判据需要它——见 gap-rat 规则。"""
    marks = list(_HEADING.finditer(text))
    if not marks:
        return [(0, "", text)]
    out = []
    if marks[0].start() > 0:
        out.append((0, "", text[:marks[0].start()]))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        out.append((len(m.group(1)), m.group(2), text[m.end():end]))
    return out


def section_subtree_text(leveled, i: int) -> str:
    """第 i 节**及其所有子节**的正文合计（到下一个同级或更高级标题为止）。

    为什么需要子树：综述的父节常叫「四、文献的批评与空白」，标题里带"空白"，
    而真正配了理论依据的是它的**子节** 4.2。只看"本节与下一节"会把
    **按模板写好的综述**判成违规（2026-10-08 实测：多篇模板即此结构）。
    """
    level = leveled[i][0]
    parts = [leveled[i][2]]
    j = i + 1
    while j < len(leveled) and leveled[j][0] > level:
        parts.append(leveled[j][2])
        j += 1
    return "\n".join(parts)


def guess_form(path: str, text: str) -> str:
    """判形态：**内容特征优先，文件名兜底**（理由见 _FORM_SIGNATURES 处说明）。"""
    name = path.rsplit("/", 1)[-1]
    # 0) 技能自带的说明与模板按「规范文档」处理（路径特征，与内容无关）
    if {"references", "assets"} & set(re.split(r"[\\/]", path)):
        return "doc"
    # 0b) ★**脚本自动生成的报告不是 review 成稿**——按「规范文档」处理。
    #     实测（2026-10-09）：对一个工作区做全库 lint 时，`batch_extract` 生成的
    #     `_提取报告.md`（27 字）被判成 B 形态，报"字数 27 低于 B 形态下限 800"——
    #     全库体检因此一片红，而它根本不是人写的 review。
    #     与 DOC_FORM_SKIP 处理模板是同一类问题：**把工具自己的产出当成人稿检查**。
    if name.startswith("_提取报告") or "批量抽文本报告" in text[:200]:
        return "doc"
    # 1) ★内容特征（顺序即优先级）。
    #    ⚠️ **只在标题行里匹配**：签名全部取自各模板的小节标题，而正文**会提到形态名**
    #    （实测踩过：一份"速览"在正文写"处理方式：……章节级解码卡＋一页速览"，
    #     于是被内容规则改判成 chapter-card，再套上 chapter-card 的规格报缺密码）。
    #    限定在标题行，则"正文说明自己属于哪个形态"不会反过来改变自己的形态。
    headings = "\n".join(m.group(0) for m in _HEADING.finditer(text))
    for form, marks, need in _FORM_SIGNATURES:
        if sum(1 for m in marks if m in headings) >= need:
            return form
    # 2) 文件名（内容判不出时）
    for form, marks in _FILENAME_MARKS:
        if _hit_filename(name, marks):
            return form
    # 3) 结构兜底：有「作者提出的主要问题」而无多篇文献表 → 详版
    if "作者提出的主要问题" in text:
        return "A"
    return "B"


def _inside_paren(text: str, pos: int) -> bool:
    """判断 pos 处的缩写是否处在括注里（`研究结果（ROF）` 的合规用法）。

    ⚠️ 不能只看紧邻的前一个字符。并列括注 `（WTD / WTDD）`、`（SPL / CPL）`、
    `（MOP / RPP）` 里，第二个缩写前面是分隔符而不是左括号，会被误判为裸缩写——
    **而模板的标题正是这个写法**，于是照模板写出的成稿必然被罚
    （2026-10-08 用真实文献跑形态 A 时实测：3 处 ERROR 全部来自这里）。

    改为向左扫描并计数括号深度：先遇到左括号（且此前无未闭合右括号）→ 在括注内；
    先遇到右括号则说明该缩写落在括注**之外**；扫描到行首 → 不在括注内。
    """
    depth = 0
    i = pos - 1
    while i >= 0 and text[i] != "\n":
        ch = text[i]
        if ch in "）)":
            depth += 1
        elif ch in "（(":
            if depth == 0:
                return True
            depth -= 1
        i -= 1
    return False


# ---------------------------------------------------------------- 检查

def lint_text(path: str, text: str, form: str = "auto", strict: bool = False,
              min_chars: int = None, max_chars: int = None,
              skip=(), allow_abbr=()) -> dict:
    form = guess_form(path, text) if form == "auto" else form
    off = set(skip)
    if form == "doc":
        off |= DOC_FORM_SKIP
    unknown = off - set(ALL_RULES)
    if unknown:
        raise ValueError(f"未知规则名：{sorted(unknown)}（可用：{list(ALL_RULES)}）")

    lo_default, hi_default = FORM_RANGES.get(form, (0, 10 ** 9))
    lo = lo_default if min_chars is None else min_chars
    hi = hi_default if max_chars is None else max_chars

    errors, warnings, infos = [], [], []
    chars = count_cjk(text)
    sections = split_sections(text)

    # 0) 产物卫生：脚本污染（HTML 实体 / 反斜杠引用字面泄漏）。
    #    这两类不经人手，是自动化改写留下的垃圾，任何形态下都是错误（故不受 doc 豁免）。
    if "hygiene" not in off:
        body = strip_code(text)
        ents = _HTML_ENTITY.findall(body)
        if ents:
            errors.append(f"HTML 实体残留 {len(ents)} 处（{'、'.join(sorted(set(ents))[:4])}）："
                          "多为脚本把非 ASCII 转义后未回转，应还原为原字符")
        leaks = _BACKREF_LEAK.findall(body)
        if leaks:
            errors.append(f"反斜杠引用字面泄漏 {len(leaks)} 处（{'、'.join(sorted(set(leaks))[:4])}）："
                          "多为 re.sub 替换体写成 r\"\\1\"+text 而非函数，已把 \\1 当字面量输出")

    # 1) 字数区间
    if "chars" not in off:
        if chars < lo:
            errors.append(f"字数 {chars} 低于 {form} 形态下限 {lo}："
                          "详版偏短通常意味着批评点没挖到具体处")
        if chars > hi:
            warnings.append(f"字数 {chars} 超过 {form} 形态上限 {hi}："
                            "先按整节重写压缩，压不动就在文末标注字数与取舍顺序")

    # 2) 缩写残留（中文名之外的纯缩写）
    #    SKILL.md 允许两种例外：与英文原文对话、表格密集排版。表格行只告警不阻断，
    #    代码块（示例/清单）整体跳过。
    bare, table, paren = [], [], []
    allowed = {a.strip().upper() for a in allow_abbr if a.strip()}
    # 缩写与密码栏检查针对**正文**：注释里的缩写是写给填写者的说明，不是成稿内容
    body = strip_comments(text)
    if "abbr" not in off:
        in_fence = False
        for line in body.splitlines():
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            for m in _ABBR.finditer(line):
                name = m.group(1)
                # 领域术语可能与密码缩写同形：实测 RPP＝Research–Practice Partnership
                # （研究—实践伙伴关系），与密码表里的 RPP（待探讨的相关问题）不是一回事。
                # 这类同形词按"误报"处理会逼人改出事实错误，故给显式白名单。
                if name.upper() in allowed:
                    continue
                if _inside_paren(line, m.start()):
                    paren.append(name)
                elif line.lstrip().startswith("|"):
                    table.append(name)
                else:
                    bare.append(name)
    if bare:
        errors.append(f"纯缩写残留 {len(bare)} 处（应写中文密码名，缩写仅作首次括注）："
                      f"{', '.join(sorted(set(bare))[:8])}")
    if table:
        warnings.append(f"表格内纯缩写 {len(table)} 处（表格密集排版允许，"
                        f"但正文行文仍须用中文名）：{', '.join(sorted(set(table))[:8])}")
    if paren:
        infos.append(f"括注形态缩写 {len(paren)} 处（首次出现处合规）")

    # 3) 禁用词
    #    两条精度要求（2026-10-08 用真实模型文件实测后加）：
    #    ① 不看注释——注释是写给填写者的说明，不是成稿内容；
    #    ② 区分「使用」与「提及」——模板/规则文档会说「禁止『方法有待加强』」，
    #       那是**在讲规则**；只有作者自己写出这句话才是违规。
    #       判据同 limits 规则：看该词前 12 字内有没有劝阻标记。
    if "banned" not in off:
        prose_banned = strip_comments(text)
        for word, why in BANNED:
            if not _mentions_only(prose_banned, word):
                errors.append(f"禁用表述「{word}」：{why}")

    # 4) 空白必配理论依据（逐节配对，不只查全文）
    if "gap-rat" not in off:
        # 判据取「本节 + 其全部子节」，而不是"本节 + 紧邻下一节"：
        # 综述的父节标题常含"空白"（如「四、文献的批评与空白」），
        # 配了依据的是它的子节；只看下一节会把合规稿判成违规。
        leveled = split_sections_with_levels(text)
        for i, (_, title, body) in enumerate(leveled):
            if "空白" in title or "研究空白" in title:
                scope = section_subtree_text(leveled, i)
                # 下一节要连**标题**一起看：把依据写成小节标题（「# 理论依据」）
                # 是合规写法，只看正文会漏（本条由既有测试
                # test_gap_without_theory_is_error_and_with_theory_passes 抓出）。
                nxt = ""
                if i + 1 < len(leveled):
                    nxt = leveled[i + 1][1] + leveled[i + 1][2]
                # 「理论依据」在技能里的规范写法有两种：直呼其名，或写成
                # 「因此可开展的研究是……」（SKILL.md 第 3 步原话）。只认前者同样会误判。
                if any(k in scope or k in nxt for k in GAP_RATIONALE_MARKERS):
                    continue
                errors.append(f"「{title}」小节指出了空白，但本节与下一节都没有理论依据"
                              "（空白必配理论依据，否则沦为空谈）")

    # 5) 洗衣店接衣单预警
    if "laundry" not in off:
        paras = [p.strip() for p in _PARA.split(text) if p.strip()]
        if len(paras) >= 6:
            lead = sum(1 for p in paras if _AUTHOR_LEAD.match(p))
            if lead / len(paras) > 0.5 and lead >= 4:
                warnings.append(f"{lead}/{len(paras)} 个段落以「作者（年份）」开头，"
                                "疑似按作者罗列（洗衣店接衣单）：须按主题重组")
        density = len(_YEAR_CITE.findall(text)) / max(1, chars / 300)
        if density > 6:
            warnings.append(f"年份引注密度偏高（每 300 字约 {density:.1f} 处），"
                            "检查是否在铺陈作者而非贡献观点")

    # 6) 证据可回指
    locators = len(_LOCATOR.findall(text))
    if "locator" not in off:
        need = max(3, chars // 800)
        if locators < need:
            warnings.append(f"可回指标记仅 {locators} 处，建议每 800 字至少 1 处"
                            f"（按本文篇幅约需 {need} 处）：证据必须能回原文核对")

    # 7) 密码齐备（仅详版强制）
    if "codes" not in off:
        if form in FORMS_REQUIRING_ALL_CODES:
            # ★查**全部 14 个**（10 结构 + 4 策略）。
            #   旧实现写的是 CODE_NAMES[:10]——**只查前 10 个**，于是详版可以完全不给
            #   「批评点／明显的遗漏点／待探讨的相关问题／能否理顺」，而 lint 报 OK。
            #   而这 4 个策略密码恰是技能的核心卖点：`reading-codes.md` 明说
            #   「策略性密码是**读出作者没写什么**」，`review-workflow.md` 自检清单
            #   也要求「十个结构性密码与四个策略性密码**已逐项覆盖**」；
            #   A 模板本身就有对应小节（3.3 批评点 / 5.2 遗漏点与待探讨 / 5.4 能否理顺）。
            #   2026-10-09 用一篇真实中文实证文测 A 形态时查出：我的导读确实缺这 4 个
            #   密码名（用了"可议之处""局限"等替代说法），**而 lint 报「OK 未发现问题」**。
            #   影响面已核：24 份历史 A 形态导读**全部齐备 14 个**，改严零误伤。
            missing = [n for n in CODE_NAMES if n not in text]
            if missing:
                errors.append(f"缺密码栏：{'、'.join(missing)}")
        else:
            missing = [n for n in CODE_NAMES if n not in text]
            if missing and form in ("card", "chapter-card"):
                warnings.append(f"解码卡未覆盖：{'、'.join(missing)}"
                                "（原文确实没有的应写明「原文未明确说明」）")

    # 8) 一问一答不可合并
    if "merged" not in off:
        if _MERGED.search(text):
            warnings.append("出现「研究了……并发现……」句式：提问与作答必须分开，"
                            "否则把结论伪装成前提")
        for title, body in sections:
            if "主要问题" in body and "作者给出的答案" in body and "主要问题" in title:
                infos.append("同一小节同时含提问与作答，核对是否已分栏")

    # 9) 作者自陈局限 ≠ 本发现的局限
    #    判据不能是「出现『局限』且无『本文评述』」——那是纯子串判断，会把
    #    「作者未自陈局限」「作者自陈的局限与未来研究建议」这类**已经标明归属**的
    #    句子一并报成违规（2026-10-08 用真实文献跑形态 A 时实测到：速览里一句
    #    「它未提出空白、未自陈局限」就触发了假警报）。
    #    改为逐处看该词**前 12 字内是否已有归属标记**；只有存在**归属不明**的
    #    「局限」时才告警——覆盖面不变，假警报消失。
    #    ★两个条件都要满足才告警：① 存在归属不明的「局限」；② 全文无「本文评述」。
    #    只留①会误伤——模板的小节标题「五、本文贡献与局限」本身就含该词，
    #    于是**每一份按模板写的导读都会中招**（2026-10-08 实测）。
    if "limits" not in off and _has_unattributed_limit(text) and not _PRAISE.search(text):
        warnings.append("出现未标明归属的「局限」且全文无「本文评述」标记："
                        "作者自陈局限与本文评述发现的局限必须分开写")

    return {
        "file": path, "form": form, "chars": chars,
        "sections": len(sections), "locators": locators,
        "errors": errors, "warnings": warnings, "infos": infos,
        "ok": not errors,
    }


def render(result: dict) -> str:
    out = [f"── {result['file']}｜形态 {result['form']}｜{result['chars']} 字｜"
           f"可回指 {result['locators']} 处"]
    for tag, items in (("ERROR", result["errors"]), ("WARN", result["warnings"]),
                       ("INFO", result["infos"])):
        for item in items:
            out.append(f"   [{tag}] {item}")
    if not (result["errors"] or result["warnings"]):
        out.append("   [OK] 未发现问题")
    return "\n".join(out)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="review 文档机械自检")
    parser.add_argument("files", nargs="+", help="待检查的 markdown 文件")
    parser.add_argument("--form", default="auto",
                        choices=["auto", "A", "quick", "B", "C", "card", "chapter-card", "report", "doc"])
    parser.add_argument("--min", type=int, default=None, dest="min_chars")
    parser.add_argument("--max", type=int, default=None, dest="max_chars")
    parser.add_argument("--skip", action="append", default=[], choices=list(ALL_RULES),
                        help="临时关闭某条规则（可重复）：" + "/".join(ALL_RULES))
    parser.add_argument("--allow-abbr", action="append", default=[],
                        help="允许的领域术语缩写（与密码缩写同形时用，可重复），如 --allow-abbr RPP")
    parser.add_argument("--strict", action="store_true", help="warning 也计入失败")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    results, failed = [], 0
    for path in args.files:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            print(f"无法读取 {path}：{exc}", file=sys.stderr)
            failed += 1
            continue
        res = lint_text(path, text, form=args.form, strict=args.strict,
                        min_chars=args.min_chars, max_chars=args.max_chars,
                        skip=args.skip, allow_abbr=args.allow_abbr)
        results.append(res)
        if not res["ok"] or (args.strict and res["warnings"]):
            failed += 1

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        for res in results:
            print(render(res))
    total_err = sum(len(r["errors"]) for r in results)
    total_warn = sum(len(r["warnings"]) for r in results)
    print(f"\n合计：{len(results)} 份文件，error {total_err}，warning {total_warn}，"
          f"不通过 {failed} 份")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
