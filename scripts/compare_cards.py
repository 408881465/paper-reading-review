#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多份独立解码卡的**口径一致性比对**。

用途（协作层）：多人**互不通气**地对**同一篇**文献按**同一份判准**各出一份卡，
事后比对结论一致率。**分歧即判准缺陷的线索**——该修的是判准文档，不是使用者。

用法：
    python3 scripts/compare_cards.py 甲-解码卡.md 乙-解码卡.md [丙-解码卡.md ...]
    python3 scripts/compare_cards.py *.md --json

退出码：0 = 无硬分歧；1 = 存在硬分歧（档位／密码有无／回指基准）；2 = 用法或读文件错误。
（硬分歧可用于流水线；批评点措辞不同属**软分歧**，只报告不判失败。）

为什么需要这个脚本（2026-10-09 实测教训）：
    手工比对时，临时写的脚本**连续失败三次**——
      ① 档位被读成 T1/T1：朴素子串匹配在「T1核心／T2重要／T3背景」这种**菜单串**上就命中了；
      ② 批评点被读成 1 条 vs 9 条：一人把密码写在**表格行**里，另一人另立了 `###` 小节；
      ③ 遗漏点整栏读不到：同上，且**单元格内部**用 ①②③ 分条。
    根因是**同一份模板允许两种结构**，所以比对**不能靠行位置**，必须两种都认。
"""

import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------- 十四个密码

CODE_NAMES = ["作者提出的主要问题", "现有文献综述", "现有文献批评", "空白", "理论依据",
              "研究结果", "一致的研究发现", "相反的研究发现", "作者给出的答案",
              "未来研究建议", "批评点", "明显的遗漏点", "待探讨的相关问题", "能否理顺"]

# 密码名在真实卡里可能带括注（如「现有文献批评（CPL）」／「批评点（本文评述，五条）」），
# 故匹配时用「前缀命中」而非全等。
_SHORT = {
    "作者提出的主要问题": ["作者提出的主要问题", "主要问题"],
    "现有文献综述": ["现有文献综述", "文献综述"],
    "现有文献批评": ["现有文献批评"],
    "空白": ["空白"],
    "理论依据": ["理论依据"],
    "研究结果": ["研究结果"],
    "一致的研究发现": ["一致的研究发现", "一致发现"],
    "相反的研究发现": ["相反的研究发现", "相反发现"],
    "作者给出的答案": ["作者给出的答案", "给出的答案"],
    "未来研究建议": ["未来研究建议", "未来研究"],
    "批评点": ["批评点"],
    "明显的遗漏点": ["明显的遗漏点", "遗漏点"],
    "待探讨的相关问题": ["待探讨的相关问题", "待探讨问题", "待探讨"],
    "能否理顺": ["能否理顺"],
}

# ★**只有这 5 栏可以判「未提出」**——它们是「作者没做某件事」这一类判定。
#   其余栏（主要问题／综述／理论依据／研究结果／答案／批评点／遗漏点／待探讨／能否理顺）
#   只要写了内容就是「有」：你是"写出来"才算填了这一栏，不存在"作者没做"的读法。
#   2026-10-09 实测踩过：不加这个白名单时，「研究结果」被两份卡都判成"未提出"——
#   只因为段里出现了「无实证」这样的评价语；「明显的遗漏点」更离谱，
#   乙的**标题**写着「（作者未看见的维度，与作者自陈局限分开写）」，
#   段首第一个词就是「作者未看见」→ 整栏被判成"未提出"。
_NEG_CODES = {"现有文献批评", "空白", "一致的研究发现", "相反的研究发现", "未来研究建议"}

# 「作者未提出／未报告」一族的判据。**必须与"作者自陈"区分开**：
# 「作者未报告相反的研究发现」是判定，不是作者说了什么。
# ⚠️ 否定语必须出现在**段首**（判定就写在那一栏的开头）；出现在中后段的，
#    往往是"其中某一条没做"，不足以把整栏判成"未提出"。
_NEG = re.compile(r"^[^。；;]{0,60}?(?:作者未(提出|明示|报告|对)|未提出|未报告|"
                  r"未点名批评|未批评任何|未对[^。；;]{0,10}批评|未针对[^。；;]{0,10}批评|"
                  r"无实证发现|严格缺位|作者没有)")

# 档位的**菜单串**：T1核心／T2重要／T3背景 这种并列必须整段排除，
# 否则朴素匹配会命中菜单里的第一个 T 编号（实测踩过）。
_MENU = re.compile(r"T\s*[123][^T\n]{0,8}[／/、,，][^T\n]{0,8}T\s*[123]")
_TIER = re.compile(r"T\s*([123])")
_TIER_CN = {1: "T1核心", 2: "T2重要", 3: "T3背景"}


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("**", "").replace("`", "")).strip()


def strip_menus(seg: str) -> str:
    """把「T1核心／T2重要／T3背景」这类并列菜单整段剥掉，再去认档位。"""
    out, prev = seg, None
    while prev != out:
        prev = out
        out = _MENU.sub(" ", out)
    return out


def find_tier(text: str):
    """→ (档位中文名 或 None, 依据摘录)。

    ★顺序很重要：**先剥菜单**，再看题录表／「档位」小节。
    """
    cands = []
    # ① 题录表格里的「档位」行（最可靠）
    for m in re.finditer(r"(?m)^\|[^|\n]*档位[^|\n]*\|([^\n]*)", text):
        cands.append(m.group(1))
    # ② 显式声明行：「① 档位：T2 重要」「**档位：T3 背景**」
    for m in re.finditer(r"档位[：:][^\n]{0,80}", text):
        cands.append(m.group(0))
    # ③ 「档位判据」小节标题
    for m in re.finditer(r"(?m)^#{2,4}[^\n]*档位[^\n]*\n", text):
        cands.append(m.group(0))
    for seg in cands:
        s = strip_menus(seg)
        # ★要求 T 编号不能被"T1核心／T2重要"那类紧邻并列再带偏
        m = _TIER.search(s)
        if m:
            return _TIER_CN[int(m.group(1))], _clean(seg)[:90]
    # ④ 兜底：全文剥掉菜单后的第一个 T 编号（最弱，但好过没有）
    s = strip_menus(text)
    m = _TIER.search(s)
    if m:
        return _TIER_CN[int(m.group(1))], "（兜底：全文首个非菜单 T 编号）"
    return None, ""


def field_segment(text: str, name: str, flat: bool = False) -> str:
    """取某密码栏的正文——**小节与表格行两种写法都认**（本脚本的重点）。

    ★保留换行：条目切分靠 `re.M` 的行锚点。实测踩过——早先在这里就把空白压平了，
    段变成一整行，`re.M` 失效，5 条批评点只切出 1 条。
    `flat=True` 时才压平（供展示与判定）。
    """
    keys = _SHORT.get(name, [name])

    def _is_dedicated(head: str, k: str) -> bool:
        """标题是不是**专讲这一栏**？

        ★把密码名与括注剥掉后，若还剩实质文字，就不是专节。
        实测踩过：「## 3. 档位判据与空白判定（本卡必答项）」含「空白」，
        会被误当空白专节，于是把档位那段读成了空白的答复 → 乙的「空白」被判成"有"。
        """
        s = re.sub(r"[（(][^）)]*[）)]", "", head)        # 去括注
        s = s.replace(k, " ")
        s = re.sub(r"^[#\s\d.、:：|*\-]+", "", s)         # 去标题记号与序号
        # ★阈值只能留 ≤2：实测「## 3. 档位判据与空白判定」剥掉"空白"后还剩
        #   「档位判据与判定」8 个字，若阈值 8 就会误放行，把档位那段读成空白的答复。
        return len(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", s)) <= 2

    # ① 小节（只认专节）
    for k in keys:
        for m in re.finditer(r"(?m)^#{2,4}[^\n]*" + re.escape(k) + r"[^\n]*\n(.*?)(?=^#{2,4}\s|\Z)",
                             text, re.S):
            head = text[m.start():text.index("\n", m.start())]
            if not _is_dedicated(head, k):
                continue
            body = m.group(1)
            if body.strip():
                return _clean(body) if flat else body
    # ② 表格行（结构化答复的规范位置）
    for k in keys:
        m = re.search(r"(?m)^\|[^|\n]*" + re.escape(k) + r"[^|\n]*\|([^\n]*)", text)
        if m and m.group(1).strip():
            return _clean(m.group(1))
    return ""


def judge(text: str, name: str):
    """→ (判定, 摘录)。判定 ∈ 有／未提出／缺失。"""
    seg = field_segment(text, name, flat=True)
    if not seg:
        return "缺失", ""
    if name not in _NEG_CODES:
        return "有", seg[:110]
    return ("未提出" if _NEG.search(seg) else "有"), seg[:110]


def items(text: str, name: str):
    """把某栏拆成条目——**同时支持**：单元格内 ①②③、行内 1. 2. 3.、`- ` 列表。"""
    seg = field_segment(text, name)
    if not seg:
        return []
    # ★**先按行首编号切**（`1. ` `2. ` `- `）：条目通常是独立的行。
    #   2026-10-09 实测踩过：原先**先按 ①②③ 切**，而正文里 ①②③ 常作**次级枚举**
    #   出现（"分三类：①…②…③…"），结果把整栏切成了 1 条，而实际是 5 条。
    out = [_clean(x) for x in re.findall(r"(?m)^\s*(?:\d+[.、)]|[-*])\s*(.{8,300})", seg)]
    if len(out) >= 2:
        return out
    # ★表格**单元格内连写**的编号（`| 批评点 | **1. … 2. … 3. …** |`）：没有换行，
    #   行首锚点切不出多条。2026-10-09 重测踩到——读者把 5 条批评点压在一个单元格里，
    #   本脚本只读出 1 条（这正是先前"先按行首编号"那一改的镜像缺口）。
    parts = re.split(r"\*{0,2}(?=\d+[.、)])", seg)
    cell = []
    for q in parts:
        q = _clean(re.sub(r"^[\d.、)\-*\s]+", "", q))
        if len(q) >= 8:
            cell.append(q)
    if len(cell) > len(out):
        out = cell
    if len(out) >= 2:
        return out
    # 表格**单元格内**常用 ①②③ 连写（没有换行），此时才按它们切
    parts = re.split(r"(?=[①②③④⑤⑥⑦⑧⑨⑩])", seg)
    out = []
    for p in parts:
        p = _clean(re.sub(r"^[①②③④⑤⑥⑦⑧⑨⑩\d.、)\-*\s]+", "", p))
        if len(p) >= 8:
            out.append(p)
    return out


def bigrams(s: str):
    s = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", s)
    return {s[i:i + 2] for i in range(max(0, len(s) - 1))}


def match_items(a, b, thresh=0.34):
    """→ (匹配数, [(甲序号, 乙序号, 相似度)])。用二元字组 Jaccard 近似。"""
    pairs = []
    for i, x in enumerate(a):
        bx = bigrams(x)
        best, bj = 0.0, None
        for j, y in enumerate(b):
            by = bigrams(y)
            if not bx or not by:
                continue
            jac = len(bx & by) / len(bx | by)
            if jac > best:
                best, bj = jac, j
        if best >= thresh:
            pairs.append((i + 1, bj + 1, round(best, 2)))
    return len(pairs), pairs


def ref_basis(text: str):
    """回指基准：优先认显式 K，其次认「PAGE n ↔ p.m」。"""
    m = re.search(r"K\s*=\s*([+-]?\d+)", text)
    if m:
        return f"K={m.group(1)}"
    m = re.search(r"PAGE\s*(\d+)[^\n]{0,12}p\.?\s*(\d+)", text)
    if m:
        return f"K={int(m.group(2)) - int(m.group(1)):+d}"
    # ★「各页页脚裸数字依次为 70/71/72」这类写法：信息完整、可复算，只是没写 K。
    #   2026-10-09 第三轮重测踩到——按"必须写 K"更利于机器读，但**脚本不该因此判它缺失**。
    m = re.search(r"裸数字[^\n]{0,20}?((?:\d{1,4}[／/、,\s]+){1,}\d{1,4})", text)
    if m:
        nums = [int(x) for x in re.findall(r"\d{1,4}", m.group(1))]
        if len(nums) >= 2 and nums[-1] - nums[0] == len(nums) - 1:
            return f"K={nums[0] - 1:+d}"      # PAGE 1 ↔ 首个裸数字 → K = first - 1
    if re.search(r"章节名", text):
        return "章节名"
    return "?"


def cjk_len(text: str) -> int:
    return len(re.sub(r"\s", "", text))


# ---------------------------------------------------------------- 主流程

HARD = {"档位", "作者提出的主要问题", "现有文献综述", "现有文献批评", "空白", "理论依据",
        "研究结果", "一致的研究发现", "相反的研究发现", "作者给出的答案", "未来研究建议",
        "回指基准", "能否理顺"}


def analyze(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    tier, why = find_tier(text)
    codes = {c: judge(text, c)[0] for c in CODE_NAMES}
    codes_raw = {c: judge(text, c)[1] for c in CODE_NAMES}
    return {
        "file": os.path.basename(path),
        "tier": tier, "tier_why": why,
        "codes": codes, "codes_raw": codes_raw,
        "crit": items(text, "批评点"),
        "miss": items(text, "明显的遗漏点"),
        "ref": ref_basis(text),
        "chars": cjk_len(text),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="多份独立解码卡的口径一致性比对（协作层用）")
    ap.add_argument("cards", nargs="+", help="两份或更多同篇文献的解码卡（Markdown）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    ap.add_argument("--overlap", type=float, default=0.34,
                    help="条目内容重合的判定阈值（二元字组 Jaccard，默认 0.34）")
    args = ap.parse_args(argv)

    if len(args.cards) < 2:
        print("至少要两份卡才能比对（口径一致性测的就是分歧）。", file=sys.stderr)
        return 2
    data = []
    for p in args.cards:
        if not os.path.exists(p):
            print(f"文件不存在：{p}", file=sys.stderr)
            return 2
        data.append(analyze(p))

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    W = 14
    head = "  " + "项".ljust(16) + "".join(d["file"][:W].ljust(W + 2) for d in data) + "一致?"
    print("=" * (18 + (W + 2) * len(data) + 6))
    print("  口径一致性比对")
    print("=" * (18 + (W + 2) * len(data) + 6))
    print(head)
    print("  " + "─" * (len(head) - 2))

    issues, hard = [], []

    def row(label, vals, is_hard):
        uniq = {v for v in vals}
        ok = len(uniq) == 1
        line = "  " + label.ljust(16) + "".join(str(v)[:W].ljust(W + 2) for v in vals)
        line += "✓" if ok else "★不一致"
        print(line)
        if not ok:
            issues.append((label, vals))
            if is_hard:
                hard.append(label)

    row("档位", [d["tier"] or "?" for d in data], True)
    row("回指基准", [d["ref"] for d in data], True)
    for c in CODE_NAMES:
        row(c, [d["codes"][c] for d in data], c in HARD)
    row("字数", [d["chars"] for d in data], False)

    print()
    print("  条目级重合（二元字组 Jaccard ≥ %.2f）" % args.overlap)
    print("    ⚠️ 两人**用不同措辞说同一件事**时，相似度会偏低——故除计数外列出最高相似的对，")
    print("       供人工判断「哪些其实是同一条」。判定分歧时以内容为准，不以分数为准。")
    base = data[0]
    for other in data[1:]:
        for label, key in (("批评点", "crit"), ("遗漏点", "miss")):
            a, b = base[key], other[key]
            n, pairs = match_items(a, b, args.overlap)
            mark = "✓" if (n == len(a) == len(b)) else "★"
            print(f"    {mark} {label}：{base['file']} {len(a)} 条 ↔ {other['file']} {len(b)} 条，"
                  f"重合 {n} 条")
            # 列出每条在对方里的最佳匹配与分数（未过阈值的也列，标"疑似"）
            for i, x in enumerate(a):
                bx = bigrams(x)
                best, bj = 0.0, None
                for j, y in enumerate(b):
                    by = bigrams(y)
                    if bx and by:
                        jac = len(bx & by) / len(bx | by)
                        if jac > best:
                            best, bj = jac, j
                if bj is None:
                    continue
                tag = "✓" if best >= args.overlap else ("疑似同一条" if best >= args.overlap * 0.6 else "")
                if tag:
                    print(f"        {base['file']}#{i+1}({best:.2f}) ↔ {other['file']}#{bj+1}  {tag}")

    print()
    print("  档位依据（各自写的理由，用于仲裁）")
    for d in data:
        print(f"    {d['file']}：{d['tier']}｜{d['tier_why']}")

    total = 1 + 1 + len(CODE_NAMES)
    print()
    print(f"  → 硬性判定 {total} 项，不一致 {len(hard)} 项"
          + (f"：{'、'.join(hard)}" if hard else "  ✓"))
    if issues:
        print("  → 全部分歧项：" + "、".join(k for k, _ in issues))

    print()
    print("  ⚠️ 分歧不是谁错——**它是判准文档的缺陷线索**。")
    print("     处置：回到 references/batch-workflow.md 与 reading-codes.md，")
    print("     把该处判准补成**可操作的反例或优先级**，再重测一次。")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
