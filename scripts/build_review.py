#!/usr/bin/env python3
"""RCOS（阅读密码整合表）校验与主题聚类提示。

RCOS 是多篇文献综述（形态 B）的必备中间工件。本脚本对 RCOS 表做两类检查：

1. **完备性检查**——每篇该填的密码栏是否填了；是否有主题聚类失衡（一树吊死）。
2. **主题聚类提示**——对「研究结果（作者发现了什么） / 现有文献综述」栏做词频统计，给出现有
   文献综述的候选主题（8–10 个）和作者对现有文献的批评 / 现有文献研究空白的候选（3–5 组），
   辅助完成「按主题重组而非按作者罗列」的关键转换。

用法：
    # 校验并输出聚类提示
    python3 build_review.py rcos <rcos.csv>

    # 只做完备性检查
    python3 build_review.py rcos <rcos.csv> --check-only

    # 生成空白 RCOS 模板
    python3 build_review.py init <输出路径>

CSV 约定：首行为表头，列名支持中英文（见 FIELD_ALIASES）。
"""

import argparse
import csv
import os
import re
import sys
from collections import Counter

# ---------------------------------------------------------------- 列名映射

# 内部规范名 -> 可接受的表头写法（小写、去空格后匹配）
FIELD_ALIASES = {
    "no":      {"序号", "编号", "no", "id", "index", "#"},
    "author":  {"作者", "author", "authors", "作者（年）"},
    "year":    {"年份", "年", "year", "date"},
    "title":   {"标题", "题目", "title", "篇名"},
    "source":  {"来源", "期刊", "出处", "source", "journal", "venue"},
    "spl":     {"spl", "现有文献综述", "前人研究", "spl主题"},
    "cpl":     {"cpl", "作者对现有文献的批评", "cpl/gap", "批评"},
    "gap":     {"gap", "现有文献研究空白", "研究空白", "空白", "gap空白"},
    "rof":     {"rof", "研究结果（作者发现了什么）", "研究结果", "主要发现", "rof发现"},
    "rfw":     {"rfw", "作者对未来研究的建议", "未来研究"},
    # ★以下是 §5.3「形态 D 的 RCOS 最小列集」里规定、但校验器此前**不认得**的 8 列。
    #   2026-10-08 实测：按 §5.3 写的 20 列表被报「未识别的列（将忽略）」，
    #   其中就有 §5.3 点名"**不可省**"的 `理论依据`——等于工具在劝用户删掉
    #   技能自己的核心纪律（「空白必配理论依据」）那一列。
    #   ★2026-10-10 再补 `wtd`：列集**原本只有「作者实际做了什么」而无「作者要做什么」**，
    #   于是表中无法核对「承诺 vs 交付」——技能最强调的一对读法在整合表里根本没法做，
    #   形态 C 的「作者要做什么（WTD）」行也无处取数。列集已由 20 列改为 21 列。
    "rat":     {"rat", "理论依据（研究的必要性）", "理论依据", "理论", "依据"},
    "rcl":     {"rcl", "与现有文献观点一致的研究发现", "一致发现", "与既有文献的一致"},
    "rtc":     {"rtc", "与现有文献观点相反的研究发现", "相反发现", "与既有文献的冲突"},
    "wtd":     {"wtd", "作者要做什么", "要做什么"},
    "wtdd":    {"wtdd", "作者实际做了什么", "实际做了什么"},
    "mop":     {"mop", "明显的遗漏点", "遗漏点", "明显遗漏"},
    "wil":     {"wil", "逻辑能否走通（能否自圆其说）", "逻辑能否走通", "能否理清"},
    "topic":   {"topic", "主题分类", "主题"},
    "oneline": {"oneline", "一句话定位", "定位"},
    # ★`poc`（批评点）与 `rpp`（待探讨的相关问题）是**两个不同的密码**，必须各自成规范名。
    #   2026-10-09 用一份 20 列的 RCOS 查出：此前把「待探讨的相关问题」也塞进 `poc` 的别名集，
    #   两列撞同一规范名，而 `resolve_columns` 只保留第一个 →
    #   **「待探讨的相关问题」整列数据静默丢失**，且**既不在映射也不在 `unknown`**，
    #   报告还显示"未识别 0 列"——完全看不出来。
    "poc":     {"poc", "批评点", "批评点/遗漏点"},
    "rpp":     {"rpp", "待探讨的相关问题", "待探讨问题", "待探讨",
                # ↓ norm_header() 会剥离斜杠与顿号，故组合形态要写成剥离后的键
                "批评点/待探讨的相关问题", "批评点/待探讨问题",
                "批评点待探讨的相关问题", "批评点待探讨问题",
                "批评点与待探讨问题", "批评点与待探讨的相关问题",
                "poc/rpp", "poc·rpp"},
}

# 主题聚类只对这几栏做词频统计
# ★`wtd`（作者要做什么）**有意不进聚类**：该栏有技能规定的「兜底写法」
#   （问句缺失时写「作者未以问句明确提出主要问题」+「（本文评述转写：……？据 p.X–Y 归纳）」），
#   用政策／论述类语料时这一族套话会挤占候选主题词——与下面 STOPWORDS 里
#   已被清掉的那批同源。要按研究问题聚类时，另用 --keep 显式放行，不默认开。
CLUSTER_FIELDS = ["spl", "rof", "cpl", "gap"]

# n-gram 窗口上限与展示上限。
# MAX_GRAM 要大：只有让完整短语生成出来，才判得出哪些短词只是它的碎片。
# DISPLAY_MAX 要小：主题标签是两个到六个字的词，不是整句套话。
MAX_GRAM = 12
DISPLAY_MAX = 6

# 缺栏时的严重程度：critical = 没有它就没法做综述
REQUIRED = ["author", "year", "spl", "rof"]
# 建议栏（不是必需栏，但不可省）——缺失只报「无此列」，不算错。
# ★按**推导链顺序**排列，覆盖率表就是这个顺序，读起来即是链条本身：
#   作者对现有文献的批评 → 现有文献研究空白 → 理论依据 → 作者要做什么 → 作者对未来研究的建议 → 批评点
# `rat`（理论依据）：§5.3 明说"在校验器里不是必需栏，但不可省"。
# `wtd`（作者要做什么）：同样如此——缺了它，表中只剩「交付」而无「承诺」，
#   核对不了对齐度；但主题聚类用不到它，故不列为必需（存量 RCOS 不会因此报错）。
RECOMMENDED = ["cpl", "gap", "rat", "wtd", "rfw", "poc"]

# 中文停用词：高频且无主题区分度
#
# 这里只放「功能词与学术套话」，不放「模型 / 框架 / 量表」这类可能是真主题的
# 普通名词——过度过滤会把真主题一起删掉，比留下噪声更糟。
# 需要按自己的语料增补时，用 `--stopwords <文件>` 传入自定义列表（每行一个词）；
# 被误滤的真主题用 `--keep <文件>` 救回。硬编码在源码里的词表没法覆盖所有语料。
STOPWORDS = set(
    "研究 分析 本文 一个 及其 通过 进行 对于 关于 以及 但是 因此 所以 "
    "可以 能够 需要 应该 不同 相关 方面 问题 方法 结果 结论 本研究 表明 "
    "指出 提出 发现 存在 具有 成为 一种 一些 这些 那些 其中 如果 由于 "
    "同时 此外 目前 主要 重要 影响 作用 关系 变化 发展 水平 程度 影响 "
    # —— 技能**自己规定**的填写句式：写进 RCOS 就不构成主题 ——
    # 2026-10-08 用课题真实的 152 行 RCOS 实测：这些短语在表里大面积复现，直接占据
    # 聚类榜首，把真主题词挤下去——「批评」121/152 行（80%）、「空白」127/152（84%）、
    # 「作者未对」「本文评述推断」各 71/152（47%）。
    # 而 batch-workflow §5 恰恰**要求**作者这样写（"作者未对任何前人文献提出批评"
    # 是合规写法、「（本文评述推断）」是必须标的归属）。技能教的写法污染了技能自己的
    # 聚类，必须由技能自己停掉，不能推给用户去 --stopwords。
    # ★要写**完整句**：expand_blocked 只向下展开子串，短语切碎后盖不住边界碎片。
    #   实测踩过：停掉「作者未对」与「前人文献提出批评」后，碎片「**未对前人文献**」
    #   仍留在榜首——它不是任何一个停用短语的子串。
    "作者未对前人文献提出批评 作者未提出空白 作者未明示空白 "
    "作者未对 作者未 本文评述推断 评述推断 前人文献提出批评 提出批评 "
    "相反发现 未报告相反 作者自陈 未自陈 未提出空白 作者未报告 "
    "研究空白 空白栏 批评点 遗漏点 待探讨 逻辑能否走通（能否自圆其说） "
    # 「明示」在本课题 152 行 RCOS 里 100% 出自同一族套话（未明示空白／作者未明示空白／
    # 未以空白句式明示）；按短语加入即可——expand_blocked 会展开出「明示」这一子串。
    "未明示空白 未以空白句式明示 "
    # ★同一族套话的**短碎片**。2026-10-09 用一份 11 行 × 20 列的 RCOS 做形态 B 规模
    #   测试时查出：上面那批「完整句」停掉后，切碎后的碎片仍占据聚类候选——
    #   实测候选榜里出现 `无数据 5`／`具体前人文献 4`／`作者明示 3`／`无实证 2`／
    #   `但未给样本 2`，**全是本表写「批评点／空白」时的评价用语，不是文献的主题内容**。
    #   它们不是任何已列短语的子串，故必须显式列出（同本段开头的教训）。
    #   `无数据`／`无样本` 等是**对文献的评价**，拿去当主题会让提纲跑偏。
    # ★同一族套话的**又一族变体**（2026-10-09 中英混排实测候选榜：`作者未对具体 7`／
    #   `真实数据 5`／`既有研究 7`）。判据同前：**技能教用户这样写，就不能让它污染聚类**。
    "作者未对具体 作者未点名 有真实数据 真实数据 无实证数据 有调查数据 "
    "既有研究 既有文献 先前研究 前人工作 实务 指南 引用 型与 "
    # ★又一批变体（2026-10-09 用「课题重要文献」18 篇重做综述时实测候选榜：
    #   `学界对 2`／`既有研究对 2`／`潜在类型与 2`）——同族套话的切碎形态，判据同前。
    "学界对 既有研究对 潜在类型与 给出 现有研究对 具体前人研究提出批评 "
    "无数据 无实证 无样本 未给样本 但未给样本 未注明样本 无前后测 无统计 无测量 无量化 "
    "作者明示 作者未提出 作者未报告 作者未明示 作者期待 "
    "具体前人文献 具体文献 未与具体文献 未与具体文献对照 未对具体前人文献提出批评 "
    "现状判断 非空白句式 属现状判断 本文评述推断 属本文评述 "
    "未操作化 未被操作化 未制度化 未被制度化 "
    # —— 学术套话：跨语料高频复现，但不构成主题 ——
    "已有研究 已有文献 现有研究 现有文献 前人研究 相关研究 大量研究 多数研究 "
    "研究表明 研究发现 研究指出 研究认为 研究者 学者 学界 领域内 "
    "缺乏 不足 尚未 难以 较少 多数 部分 近年 国内 国外 国内外 "
    "综述 梳理 考察 关注 聚焦 采用 使用 基于 构建 建立 提出 分析 验证 "
    "检验 探索 揭示 机制 作用 关系 差异 特征 维度 指标 标准 建议 "
    "因此 但是 而且 从而 进而 使得 导致 体现 反映 表明 显示 认为 强调 "
    "the and for that with this from are was were has have not but which "
    # ★英文学术套话。2026-10-09 中英混排实测：内置表此前只有上面 12 个英文功能词，
    #   于是 `gap`／`focus`／`still` 这类**英文套话**直接进了候选榜（gap 出现 4 次）。
    #   ⚠️ **只停套话与功能词**，不停可能是真主题的名词：teacher／student／
    #   collaboration／curriculum／co-teaching／stem／ai 等一律**保留**
    #   （本主题里 teacher 出现 14 次，正是真主题）。
    "gap gaps study studies research researches article articles paper papers review "
    "however therefore furthermore moreover thus hence although though while whereas "
    "finding findings conclusion conclusions implication implications result results "
    "focus focuses still also more most such other others using used use based "
    "et al between among within across during about into over under "
    "present presents presented show shows showed shown suggest suggests suggested "
    "provide provides provided require requires required include includes included "
    "they their its been also such these those using used study research "
    "paper article results method methods analysis data".split()
)

# 通用研究套话碎片：跨词边界切出的 n-gram（如「研究已」「响新闻价」），
# 无主题区分度，聚类时应排除。
#
# ⚠️ 这份词表只放**通用**套话。旧版本里混入了「新闻价 / 闻价值 / 新闻价值研」
# 这类只对作者当时那篇测试文献成立的碎片——词表一旦过拟合到某一份语料，
# 换个主题就会出现两头的错：该滤的滤不掉（实测换成教育类语料后，
# 前 9 名候选里有 6 个是「已有研究提出」式的碎片），
# 而真主题「新闻价值」反被误杀。
# 按自己的语料补充时请用 --stopwords <文件>，不要去改源码。
GENERIC_FRAGMENTS = set(
    "研究 研究已 研究存 存在 已有 有研究 没有 不能 不足 缺乏 "
    "影响 影响因素 因素 考察 检验 检验了 表明 指出 显示 证明 报告 "
    "分析 分析了 使用 采用 引入 建立 构建 提出 探讨 讨论 关注 重视 "
    "机制 机制未 未验证 未考察 未整合 未明确 未量化 不明确 "
    "方法研究 研究框架 框架 维度 层面 角度 视角 价值研究 "
    "较为 相对 更为 更加 十分 非常 尤其 其中 以上 以下 之间 之后 之前".split()
)

# 以研究套话动词/系词开头、且余下部分仍为套话的 n-gram（如「研究已积累」
# 「影响未验证」），其信息量全在余下部分，词头纯属噪声。
# 注意：只砍「套话前缀 + 套话余部」；「影响新闻价值」这类余部为实质内容的
# 组合必须保留——不能按前缀一刀切。
GENERIC_PREFIXES = (
    "研究", "分析", "影响", "考察", "检验", "方法", "结果", "存在",
    "缺乏", "提出", "采用", "使用", "建立", "发现", "表明", "机制",
)

# 学术套话前缀：出现即从文本里切掉，不是「生成后再过滤」。
# 例：「已有研究关注教师胜任力」若不先切掉「已有研究」，滑窗会切出
# 「已有研究关注」「研究关」「究关注」这类跨短语碎片，而它们彼此不是子串、
# 频次又各不相同，去重与极大重复两条规则都抓不住。
# 先切再滑窗，碎片根本不生成——这是治本的位置。
# 只列多字套话，不列「框架 / 机制 / 维度」这类可能是真主题的普通名词。
BOILERPLATE_PREFIXES = (
    "已有研究", "已有文献", "现有研究", "现有文献", "前人研究", "相关研究",
    "大量研究", "多数研究", "研究表明", "研究发现", "研究指出", "研究认为",
    # 「研究已积累」「研究已十分丰富」这类句式：研究对象词本身无主题信息，
    # 若不切分会产出「值研究已积累」这类跨短语碎片（2026-10-07 实测）。
    # 只切「研究已」这个紧邻搭配，不切单独的「已」，以免误伤「已知条件」等词。
    "研究已",
)

# 通用动词：出现在短语开头时纯属交代动作，不是主题的一部分，同样先切掉。
# 刻意不含「关注」「发现」——它们能构成真主题（关注度 / 发现学习）。
GENERIC_VERB_PREFIXES = (
    "考察", "提出", "分析", "探讨", "讨论", "揭示", "梳理", "综述",
    "检验", "验证", "建议", "指出", "表明", "认为", "强调", "采用", "使用",
)

# 用于切断 n-gram 的边界符：必须是非 CJK、非字母的字符，才能被
# tokenize 里的 [\u4e00-\u9fff]{2,} 切开。
_BOUNDARY = "·"

# 短语级虚词边界。只切「的」——它在主题标签里几乎不出现，且是最常见的
# 跨短语碎片来源（「素养的构成维度」→「素养的」「构成维度」）。
# 刻意不切「地」（会毁掉「地区差异」）和「了/着/过/得」（会毁掉
# 「了解」「显著」「过度」「得到」这类词）。
_PARTICLE_BOUNDARY = "的"

# 中文双字词抽取用：停用的单字
_STOP_CHARS = set("的了和与或在是有为对从被把将及其之乎者也之")


def norm_header(name: str) -> str:
    """表头归一：小写、去空格与常见标点。"""
    return re.sub(r"[\s_\-（）()·:：/／、,，]", "", (name or "").strip().lower())


def resolve_columns(fieldnames):
    """把实际表头映射到内部规范名。返回 (映射字典, 未识别列列表)。"""
    lookup = {}
    for canon, aliases in FIELD_ALIASES.items():
        for alias in aliases:
            lookup[norm_header(alias)] = canon

    mapping, unknown = {}, []
    for raw in fieldnames or []:
        key = norm_header(raw)
        if key in lookup:
            canon = lookup[key]
            if canon in mapping:
                # ★同规范名重复出现：保留第一个，但**后一个必须报出来**。
                #   2026-10-09 教训：静默丢弃会让"两个密码撞一个规范名"完全不可见——
                #   报告照样显示"未识别 0 列"，而那一列的数据根本没被读取
                #   （「待探讨的相关问题」就这样丢过一次）。
                # ★**逐字相同**的列名同样要报（2026-10-10 修）。此前写的是
                #   `if raw not in mapping.values()`，把"同名"这一种（最像手滑
                #   复制的一列）判成"已报过"而放行 → 报告写着"识别到的列: …"、
                #   **一条提示都没有**，而第二列的数据从头到尾没被读过。
                if raw not in unknown:
                    unknown.append(raw)
            else:
                mapping[canon] = raw
        else:
            unknown.append(raw)
    return mapping, unknown


# ---------------------------------------------------------------- 分词

def strip_boilerplate(text: str) -> str:
    """把学术套话与短语级虚词替换成边界符，防止 n-gram 跨短语生成。

    这是「治本」的一步：碎片不是在生成之后滤掉的，而是根本不生成。
    """
    for phrase in BOILERPLATE_PREFIXES + GENERIC_VERB_PREFIXES:
        if phrase in text:
            text = text.replace(phrase, _BOUNDARY)
    return text.replace(_PARTICLE_BOUNDARY, _BOUNDARY)


# ★引注剥离：**人名不是主题词**。
#   2026-10-09 中英混排实测：候选榜出现 `fuchs 2`／`zorlu 2`——它们来自我在 RCOS 里
#   写的「（Fuchs & Fuchs 1992 等）」「（Zorlu & Zorlu 2024）」。引注是**论据出处**，
#   不是文献在谈什么；混进候选会把提纲带偏。
_CITATION = re.compile(
    r"[（(][^（()）\n]{0,80}?(?:19|20)\d{2}[a-z]?[^（()）\n]{0,30}?[）)]"      # 含 4 位年份的括注
    r"|[A-Z][a-z]{2,}(?:\s*(?:&|and|、|，|,)\s*[A-Z][a-z]{2,})*\s+et\s+al\.?,?\s*(?:19|20)\d{2}"
    r"|[A-Z][a-z]{2,}\s*(?:&|and)\s*[A-Z][a-z]{2,}\s*,?\s*(?:19|20)\d{2}")


def strip_citations(text: str) -> str:
    """剥掉引注（含年份的括注与 `Name et al. Year` 式），使**人名不进主题候选**。"""
    return _CITATION.sub(" ", text or "")


def tokenize(text: str, max_size: int = MAX_GRAM):
    """中文取 2–MAX_GRAM 字滑窗（无需分词库），英文取单词。

    中文不做真分词——滑窗在主题聚类场景下够用，且零依赖。
    窗口开到 MAX_GRAM（12）而不是 6，是为了让**完整的重复短语**有机会生成：
    短语长度超过窗口时，滑窗只能切出「考察了课堂互动」「察了课堂互动模」……
    这种同长度、互相错位的碎片，谁也不是谁的子串，包含关系规则一个都抓不住。
    生成长 gram 只用于判定「谁是碎片」，展示时仍只保留 ≤ DISPLAY_MAX 字的。
    """
    tokens = []
    cleaned = strip_citations(text)
    for word in re.findall(r"[a-zA-Z]{3,}", cleaned.lower()):
        if word not in STOPWORDS:
            tokens.append(word)

    for run in re.findall(r"[\u4e00-\u9fff]{2,}", strip_boilerplate(cleaned)):
        for size in range(2, min(max_size, len(run)) + 1):
            for i in range(len(run) - size + 1):
                gram = run[i:i + size]
                if gram[0] in _STOP_CHARS:
                    continue
                tokens.append(gram)
    return tokens


def mark_dominated(candidates, max_len=DISPLAY_MAX):
    """标出「总是作为更长同频短语的一部分出现」的候选。

    g 与 g' 频次相同且 g 是 g' 的子串，说明 g 的每一次出现都被 g' 覆盖，
    g 没有独立信息量，是碎片。例：语料里反复出现「考察了课堂互动模式」，
    则「考察了课堂」「察了课堂互动」等碎片频次都与它相同，只应保留最长的那条。

    只有长度 ≤ max_len 的短语才有资格「支配」别的候选。超过展示上限的长句
    （如「考察了课堂互动模式」）本来就永远不会被输出，让它把「课堂互动模式」
    这样的好主题一并压掉是净损失。

    不必枚举所有超串：所有「在语料中真实出现、长度 ≤ MAX_GRAM」的超串都已被
    tokenize 生成并留在 candidates 里，所以只要反向枚举 g 自己的子串即可。
    """
    counts = dict(candidates)
    dominated = set()
    for gram, count in counts.items():
        if len(gram) > max_len:
            continue
        for i in range(len(gram)):
            for j in range(i + 2, len(gram) + 1):
                sub = gram[i:j]
                if sub != gram and counts.get(sub) == count:
                    dominated.add(sub)
    return dominated


def mark_misaligned(candidates, min_len=3):
    """标出错位碎片：滑窗在重复短语上切出的「缺首字/缺尾字」片段。

    `mark_dominated` 靠子串关系判定，但滑窗在长重复短语上会切出
    **互不为子串的错位碎片**：语料反复出现「深度学习在教学中的应用」时，
    「深度学习在教」「度学习在教学」「学习在教学中」三条频次相同、
    彼此互不包含，包含规则一个都抓不住（2026-10-07 实测）。

    判据：若存在 g' 使 g 等于 g' 去掉首字或尾字、且 g' 与 g **同频或更高频**，
    则 g 是 g' 被切歪的一段——它的每一次出现都属于某个更完整的短语，
    不携带独立信息。g' 频次更高时（「度学习在教」← 「深度学习在教」同为
    4，而「深度学习」为 8）更容易成立；同频时也成立，因为二者本就是
    同一个重复短语的不同切法。

    只删短的那条（g），保留完整的那条（g'）。
    """
    counts = dict(candidates)
    dominated = set()
    for gram, count in counts.items():
        if len(gram) < min_len:
            continue
        for other, other_count in counts.items():
            if other == gram or other_count < count:
                continue
            # other 比 gram 长 1 字，且去掉首字或尾字后与 gram 完全重合
            if len(other) == len(gram) + 1 and (
                other[1:] == gram or other[:-1] == gram
            ):
                dominated.add(gram)
                break
    return dominated


def dedupe_ngrams(ranked, ratio=0.6, top_n=None):
    """抑制被更长短语包住的子串碎片。

    滑窗分词会产出「新闻价值 / 闻价值研 / 价值研究」这类互相包含的碎片，
    全量列出只会淹没真正的主题词。规则：若 a 是 b 的真子串，且 b 的频次
    不低于 a 的 ratio 倍，则丢弃 a——b 更完整且几乎同样常见。
    容忍度而非严格相等，是因为长短语在语料里天然略少。

    top_n 在裁剪之后才生效：先剪枝再取前 N。否则前 N 名会被碎片占满，
    真正的主题词排在 N 名之外永远看不到。
    """
    kept = []
    for word, count in ranked:
        dominated = any(
            other != word and word in other and other_count >= count * ratio
            for other, other_count in ranked
        )
        if not dominated:
            kept.append((word, count))
    return kept[:top_n] if top_n else kept


def expand_blocked(phrases):
    """把套话短语展开成它的全部长度 ≥2 子串，返回集合。

    滑窗分词会把「已有研究」切成「已有研」「有研究」等子串；只按整词相等
    过滤必然漏掉它们（实测：「已有研究」进了停用词表，「已有研」却仍排在
    候选第一名）。展开后一次集合查找即可覆盖所有边界碎片。

    代价是可能连带滤掉真主题的一部分——用 --keep 白名单可以救回来。
    """
    out = set()
    for phrase in phrases:
        for i in range(len(phrase)):
            for j in range(i + 2, len(phrase) + 1):
                out.add(phrase[i:j])
    return out


def cluster_hint(rows, top_n=25, min_count=2, extra_stopwords=(), keepwords=()):
    """基于「研究结果（作者发现了什么） / 现有文献综述 / 作者对现有文献的批评 / 现有文献研究空白」四栏做词频统计，
    给出候选主题词。

    min_count=2：只出现一次的词不构成「反复出现的模式」，无法充当主题，
    对聚类无贡献。频次太低时应回头检查 RCOS 是否填得太笼统。

    extra_stopwords / keepwords：来自 --stopwords / --keep，只影响本次运行。
    把「本项目语料的套话」外置成文件，而不是继续往源码里的硬编码词表塞条目
    ——旧词表已经混进过只对某一篇测试文献成立的碎片。
    """
    blocked = expand_blocked(STOPWORDS | set(extra_stopwords) | GENERIC_FRAGMENTS)
    keep = set(keepwords)
    counter = Counter()
    for row in rows:
        for field in CLUSTER_FIELDS:
            value = (row.get(field) or "").strip()
            if value:
                counter.update(tokenize(value))

    # 先按频次粗筛，**不做语义词过滤**：mark_dominated 要靠完整的重复短语
    # 才能判定谁是碎片。若先把「考察了课堂互动模式」滤掉，它的碎片
    # 「考察了课堂互动」就失去覆盖它们的超串，反而会被当成独立主题留下来。
    ranked_all = [(w, c) for w, c in counter.most_common() if c >= min_count]
    dominated = mark_dominated(ranked_all)
    misaligned = mark_misaligned(ranked_all)

    topics = []
    for w, c in ranked_all:
        if w not in keep:
            if w in dominated or w in misaligned:
                continue
            if w in blocked:
                continue
            # 「研究已积累」：套话前缀 + 套话余部
            if any(w.startswith(p) and w[len(p):] in GENERIC_FRAGMENTS
                   for p in GENERIC_PREFIXES):
                continue
        if len(w) > DISPLAY_MAX:
            continue
        topics.append((w, c))
    return dedupe_ngrams(topics, top_n=top_n)


def imbalance_warning(rows, threshold=3):
    """一树吊死检查：某一位作者在 RCOS 中反复独占。

    判据取自 `review-workflow.md` 第 3.4 节「同一作者不得在相邻段落反复独占篇幅」
    ——所以检查的是**作者列**的分布，不是「现有文献综述」文本是否恰好相同。
    旧实现拿 spl 原文做精确字符串计数，自由文本几乎必然全不重复，
    于是 singles 有、heavy 没有，检查永远不触发（等于没有这个检查）。

    另外补一条：整栏文本完全一致，说明没在逐篇填写，聚类无从做起。
    """
    warnings = []
    if not rows:
        return warnings

    authors = [r.get("author") for r in rows if r.get("author")]
    if authors:
        freq = Counter(authors)
        top_author, top_count = freq.most_common(1)[0]
        if top_count >= max(threshold, len(rows) / 2) and len(freq) > 1:
            warnings.append(
                "一树吊死预警：作者「%s」独占 %d/%d 篇。综述须按主题重组，"
                "同一作者应被打散进各主题，不得反复独占篇幅——请回 RCOS 重新聚类。"
                % (top_author, top_count, len(rows))
            )

    spls = [r.get("spl") for r in rows if r.get("spl")]
    if len(spls) >= 3 and len(set(spls)) == 1:
        warnings.append(
            "「现有文献综述」栏 %d 篇文本完全相同，疑似整列复制粘贴、未逐篇填写。"
            "该栏是主题聚类的原料，请逐篇写具体内容。" % len(spls)
        )
    return warnings


# ---------------------------------------------------------------- 主流程

def load_rows(path):
    """★**按列位取数**，不用 `csv.DictReader`（2026-10-10 修）。

    实测踩过：两列都叫「现有文献研究空白」时，`DictReader` 对同名表头**后写覆盖**，
    `raw["现有文献研究空白"]` 返回的是**第二列**的值，第一列整列数据静默消失。
    `resolve_columns` 的既有口径是"保留第一个"，取数就必须跟着取**第一个**，
    否则映射与取值各按一套口径——报告与聚类都在拿另一列的数据算。
    """
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            # 与 `DictReader.fieldnames` 一致：空文件 → 无表头行。
            raise ValueError("CSV 无表头行")
        if not header:
            # 首行为空行时 `next()` 返回 `[]`；`DictReader` 同样判作无表头行。
            raise ValueError("CSV 无表头行")
        mapping, unknown = resolve_columns(header)
        # ★`list.index` 取的是**首次**出现的位置——重复表头下正是我们要的那一列。
        index = {canon: header.index(col) for canon, col in mapping.items()}
        rows = []
        for values in reader:
            if not values:                      # 与 `DictReader` 一致：跳过空行
                continue
            row = {}
            for canon, i in index.items():
                row[canon] = (values[i] if i < len(values) else "").strip()
            rows.append(row)
    return rows, mapping, unknown


# 内部规范名 -> 输出用的中文密码名（与 review 文档保持一致）
FIELD_LABELS = {
    "spl": "现有文献综述",
    "cpl": "作者对现有文献的批评",
    "gap": "现有文献研究空白",
    "rof": "研究结果（作者发现了什么）",
    "rfw": "作者对未来研究的建议",
    "poc": "批评点",
    "mop": "明显的遗漏点",
    "rpp": "待探讨的相关问题",
    "author": "作者",
    "year": "年份",
    "title": "标题",
    "source": "来源",
    "no": "序号",
    # ★规范名一律要有中文显示名，否则报告里会出现 `rat` 这种英文缩写
    #   （2026-10-09 实测：覆盖率表把「理论依据」显示成 `rat`）。
    "rat": "理论依据（研究的必要性）",
    "rcl": "与现有文献观点一致的研究发现",
    "rtc": "与现有文献观点相反的研究发现",
    "wtd": "作者要做什么",
    "wtdd": "作者实际做了什么",
    "wil": "逻辑能否走通（能否自圆其说）",
    "topic": "主题分类",
    "oneline": "一句话定位",
}


def label(field: str) -> str:
    """取字段的中文密码名；未登记的字段回退为原名。"""
    return FIELD_LABELS.get(field, field)


def pad(text: str, width: int = 16) -> str:
    """按显示宽度补空格——中文占两列，直接用 %-Ns 会错位。"""
    w = sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)
    return text + " " * max(0, width - w)


def check(path):
    rows, mapping, unknown = load_rows(path)
    problems, warnings = [], []

    print("=" * 60)
    print("RCOS 完备性检查")
    print("=" * 60)
    print(f"文件: {path}")
    print(f"文献数: {len(rows)}")

    # 空表：只有表头（或表头被误当数据）时，任何「逐行检查」都不会报错，
    # 会直接输出「✅ 检查通过」——而校验器的职责正是拦住「没填表就写综述」。
    if not rows:
        problems.append(
            "RCOS 中没有任何文献行（只读到表头）。请先用 `init` 生成模板并逐篇填写，"
            "再运行校验；空表通过检查会导致综述无米下锅。"
        )

    # 列映射
    missing_cols = [f for f in REQUIRED if f not in mapping]
    if missing_cols:
        problems.append("缺少必需列: %s（可接受表头见 FIELD_ALIASES）"
                        % ", ".join(label(f) for f in missing_cols))
    if unknown:
        # ★两个成因合并报在同一处：真正不认得的表头，以及**与前面的列指向
        #   同一密码**的重复列。后者尤其要报——它意味着有一列数据没被读取。
        warnings.append(
            "以下表头列被忽略（无法识别，或与前面的列指向同一密码）: %s"
            % ", ".join(unknown))
    print("识别到的列: %s" % (", ".join(mapping[c] for c in mapping) or "（无）"))

    # 逐行检查
    empty_required = 0
    for idx, row in enumerate(rows, 1):
        miss = [f for f in REQUIRED if not row.get(f)]
        if miss:
            empty_required += 1
            who = row.get("author") or row.get("title") or f"第{idx}行"
            warnings.append("第%d行(%s) 缺: %s"
                            % (idx, who, ", ".join(label(f) for f in miss)))

    if empty_required:
        problems.append(
            "%d/%d 篇缺少必需栏（%s）。缺「研究结果（作者发现了什么）」无法做主题聚类，"
            "缺「现有文献综述」无法构建综述骨架——请补齐后再生成综述。"
            % (empty_required, len(rows),
               "、".join(label(f) for f in REQUIRED))
        )

    # 聚类规模检查
    if len(rows) == 1:
        warnings.append("仅 1 篇文献：应使用形态 A（单篇深度导读），而非多篇主题综述。")
    elif 2 <= len(rows) < 3:
        warnings.append("仅 2 篇文献：主题聚类样本不足，建议改用形态 C（对比评述）。")

    # 一树吊死
    for msg in imbalance_warning(rows):
        warnings.append(msg)

    # 覆盖率统计
    print("-" * 60)
    print("密码栏覆盖率:")
    for field in REQUIRED + RECOMMENDED:
        name = label(field)
        if field not in mapping:
            print("  %s 无此列" % pad(name))
            continue
        filled = sum(1 for r in rows if r.get(field))
        pct = 100.0 * filled / len(rows) if rows else 0
        bar = "#" * int(pct // 5)
        print("  %s %3d/%-3d %5.1f%%  %s"
              % (pad(name), filled, len(rows), pct, bar))

    return rows, mapping, problems, warnings


def load_stopwords(path):
    """读取补充停用词：每行一个词，# 开头为注释。"""
    words = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            word = line.strip()
            if word and not word.startswith("#"):
                words.add(word)
    return words


def print_cluster(rows, extra_stopwords=(), keepwords=()):
    print()
    print("=" * 60)
    print("主题聚类提示（来自「研究结果（作者发现了什么） / 现有文献综述 / 作者对现有文献的批评 / 现有文献研究空白」四栏）")
    print("=" * 60)
    ranked = cluster_hint(rows, extra_stopwords=extra_stopwords,
                          keepwords=keepwords)
    if not ranked:
        print("（无可用词频——四栏可能均为空）")
        return
    print("候选主题词（按复现度排序）:")
    for word, count in ranked:
        print("  %-14s %2d" % (word, count))
    print()
    print("注：靠后的长候选可能是跨短语切出的碎片（如「价值与受众研」），")
    print("    人工归纳时以靠前的短词为准；本项目特有的套话可用 --stopwords 排除。")

    distinct = len(ranked)
    print()
    print("→ 目标：压缩成 8–10 个「现有文献综述」主题（形态 B 提纲第 1 层）")
    print("→ 目标：归纳成 3–5 组「作者对现有文献的批评 / 现有文献研究空白」主题（提纲第 2 层）")
    if distinct < 8:
        print("⚠  候选词偏少（%d 个），可能「研究结果（作者发现了什么） / 现有文献综述」填写过于笼统。"
              % distinct)
        print("   建议：这两栏应写具体发现与具体主题，"
              "而非『分析了XX研究』这类空泛表述。")


def cmd_init(path):
    # ★与 batch-workflow §5.3「形态 D 的 RCOS 最小列集」及 assets/rcos-template.csv
    #   **保持同一份**。此前三处各写一份：§5.3 是 20 列、模板 11 列、这里硬编码 11 列，
    #   于是 §5.3 点名"不可省"的 `理论依据` 既不在模板里也不被校验器识别
    #   （2026-10-08 实测：按 §5.3 写好的表被报「未识别的列（将忽略）」——
    #    等于工具在劝用户删掉技能核心纪律「空白必配理论依据」那一列）。
    #   ★2026-10-10 列集由 20 列扩为 **21 列**（补「作者要做什么」），改这一处必须同步
    #   §5.3 代码块、assets/rcos-template.csv、tests/test_build_review.SPEC_5_3。
    header = ["编号", "作者", "年份", "标题", "来源", "现有文献综述", "作者对现有文献的批评",
              "现有文献研究空白", "理论依据（研究的必要性）", "作者要做什么",
              "研究结果（作者发现了什么）",
              "与现有文献观点一致的研究发现", "与现有文献观点相反的研究发现",
              "作者实际做了什么", "作者对未来研究的建议", "批评点", "待探讨的相关问题",
              "明显的遗漏点", "逻辑能否走通（能否自圆其说）", "主题分类", "一句话定位"]
    # utf-8-sig 写入 BOM：与仓库自带的 rcos-template.csv 保持一致，
    # 否则用户在 Windows Excel 里打开会出现中文表头乱码。
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        csv.writer(fh).writerow(header)
    print("已生成 RCOS 模板: %s" % path)
    print("填写要点：读完当天录入；「现有文献综述」「研究结果（作者发现了什么）」两栏"
          "写具体内容，不写空泛表述。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="RCOS 校验与主题聚类提示")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rcos = sub.add_parser("rcos", help="校验 RCOS 并给出聚类提示")
    p_rcos.add_argument("csv_path", help="RCOS CSV 路径")
    p_rcos.add_argument("--check-only", action="store_true", help="只做完备性检查")
    p_rcos.add_argument("--stopwords", metavar="FILE",
                        help="补充停用词文件（每行一个，# 开头为注释）。"
                             "★两条实测注意：① 请写**完整短语**（如「人工智能教育」）"
                             "——词表按子串展开，只写短形式「人工智能」滤不掉由它延伸出的"
                             "更长碎片「人工智能教」；② 子串展开会**连带滤掉真主题**"
                             "（「课程整合」会连带滤掉「课程」），用 --keep 可救回")
    p_rcos.add_argument("--keep", metavar="FILE",
                        help="白名单文件：其中的词一律保留，不被停用词规则滤掉")

    p_init = sub.add_parser("init", help="生成空白 RCOS 模板")
    p_init.add_argument("output", help="输出 CSV 路径")

    args = parser.parse_args()

    if args.cmd == "init":
        return cmd_init(args.output)

    if not os.path.exists(args.csv_path):
        print("文件不存在: %s" % args.csv_path, file=sys.stderr)
        return 1

    extra_stopwords = ()
    if args.stopwords:
        if not os.path.exists(args.stopwords):
            print("停用词文件不存在: %s" % args.stopwords, file=sys.stderr)
            return 1
        extra_stopwords = load_stopwords(args.stopwords)

    keepwords = ()
    if args.keep:
        if not os.path.exists(args.keep):
            print("白名单文件不存在: %s" % args.keep, file=sys.stderr)
            return 1
        keepwords = load_stopwords(args.keep)

    rows, _mapping, problems, warnings = check(args.csv_path)

    if not args.check_only and rows:
        print_cluster(rows, extra_stopwords=extra_stopwords, keepwords=keepwords)

    print()
    print("=" * 60)
    if problems:
        print("❌ 需修正:")
        for p in problems:
            print("   - %s" % p)
    if warnings:
        print("⚠️  提示:")
        for w in warnings:
            print("   - %s" % w)
    if not problems and not warnings:
        print("✅ 检查通过，可进入综述提纲撰写。")
    elif not problems:
        print("✅ 无阻断问题。")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
