#!/usr/bin/env python3
"""RCOS（阅读密码整合表）校验与主题聚类提示。

RCOS 是多篇文献综述（形态 B）的必备中间工件。本脚本对 RCOS 表做两类检查：

1. **完备性检查**——每篇该填的密码栏是否填了；是否有主题聚类失衡（一树吊死）。
2. **主题聚类提示**——对「研究结果 / 现有文献综述」栏做词频统计，给出现有
   文献综述的候选主题（8–10 个）和现有文献批评 / 空白的候选（3–5 组），
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
    "cpl":     {"cpl", "现有文献批评", "cpl/gap", "批评"},
    "gap":     {"gap", "空白", "研究空白", "gap空白"},
    "rof":     {"rof", "研究结果", "主要发现", "rof发现"},
    "rfw":     {"rfw", "未来研究建议", "未来研究"},
    "poc":     {"poc", "批评点", "poc/rpp", "poc·rpp", "rpp",
                "待探讨的相关问题", "待探讨问题",
                "批评点/待探讨的相关问题", "批评点/待探讨问题",
                "批评点与待探讨问题"},
}

# 主题聚类只对这几栏做词频统计
CLUSTER_FIELDS = ["spl", "rof", "cpl", "gap"]

# 缺栏时的严重程度：critical = 没有它就没法做综述
REQUIRED = ["author", "year", "spl", "rof"]
RECOMMENDED = ["cpl", "gap", "rfw", "poc"]

# 中文停用词：高频且无主题区分度
STOPWORDS = set(
    "研究 分析 本文 一个 及其 通过 进行 对于 关于 以及 但是 因此 所以 "
    "可以 能够 需要 应该 不同 相关 方面 问题 方法 结果 结论 本研究 表明 "
    "指出 提出 发现 存在 具有 成为 一种 一些 这些 那些 其中 如果 由于 "
    "同时 此外 目前 主要 重要 影响 作用 关系 变化 发展 水平 程度 影响 "
    "the and for that with this from are was were has have not but which "
    "they their its been also such these those using used study research "
    "paper article results method methods analysis data".split()
)

# 通用研究套话碎片：跨词边界切出的 n-gram（如「研究已」「响新闻价」），
# 无主题区分度，聚类时应排除。
GENERIC_FRAGMENTS = set(
    "研究 研究已 研究存 研究存 在 存在 已有 有研究 没有 不能 不足 缺乏 "
    "影响 影响因素 因素 考察 检验 检验了 表明 指出 显示 证明 报告 "
    "分析 分析了 使用 采用 引入 建立 构建 提出 探讨 讨论 关注 重视 "
    "研究已 研究存在 影响未 影响新闻 响新闻 机制未 未验证 未考察 "
    "研究已 研究方法 方法研究 研究框架 框架 维度 层面 角度 视角 "
    "价值研究 价值研 新闻价 闻价值 闻价 值研 值研究 新闻价值研 "
    "研究已 研究积累 研究已积累 已积累 积累 缺乏对 机制未 机制 "
    "存在研究 已建立 未验证 未整合 未明确 未量化 未考察 不明确 "
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
            # 同规范名重复出现时保留第一个
            mapping.setdefault(canon, raw)
        else:
            unknown.append(raw)
    return mapping, unknown


# ---------------------------------------------------------------- 分词

def tokenize(text: str):
    """中文取 2–6 字滑窗（无需分词库），英文取单词。

    中文不做真分词——滑窗在主题聚类场景下够用，且零依赖。
    取到 6 字是为了让「新闻价值研究」这类完整短语有机会生成，
    从而在 dedupe 中吞掉它的碎片（见 dedupe_ngrams）。
    """
    tokens = []
    for word in re.findall(r"[a-zA-Z]{3,}", text.lower()):
        if word not in STOPWORDS:
            tokens.append(word)

    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        for size in range(2, 7):
            for i in range(len(run) - size + 1):
                gram = run[i:i + size]
                if gram[0] in _STOP_CHARS:
                    continue
                tokens.append(gram)
    return tokens


def dedupe_ngrams(ranked, ratio=0.6):
    """抑制被更长同频词包住的子串碎片。

    滑窗分词会产出「新闻价值 / 闻价值研 / 价值研究」这类互相包含的碎片，
    全量列出只会淹没真正的主题词。规则：若 a 是 b 的真子串，且 b 的频次
    不低于 a 的 ratio 倍，则丢弃 a——b 更完整且几乎同样常见。
    容忍度而非严格相等，是因为长短语在语料里天然略少。
    """
    kept = []
    for word, count in ranked:
        dominated = any(
            other != word and word in other and other_count >= count * ratio
            for other, other_count in ranked
        )
        if not dominated:
            kept.append((word, count))
    return kept


def cluster_hint(rows, top_n=25, min_count=2):
    """基于「研究结果 / 现有文献综述 / 现有文献批评 / 空白」四栏做词频统计，
    给出候选主题词。

    min_count=2：只出现一次的词不构成「反复出现的模式」，无法充当主题，
    对聚类无贡献。频次太低时应回头检查 RCOS 是否填得太笼统。
    """
    counter = Counter()
    for row in rows:
        for field in CLUSTER_FIELDS:
            value = (row.get(field) or "").strip()
            if value:
                counter.update(tokenize(value))

    # 过滤停用词与通用研究套话碎片
    ranked = []
    for w, c in counter.most_common():
        if w in STOPWORDS or w in GENERIC_FRAGMENTS:
            continue
        # 「影响未」的碎片「响未」这类：本身是套话短语的子串，一并剔除
        if any(w != g and w in g for g in GENERIC_FRAGMENTS):
            continue
        # 「研究已积累」：套话前缀 + 套话余部
        if any(w.startswith(p) and w[len(p):] in GENERIC_FRAGMENTS
               for p in GENERIC_PREFIXES):
            continue
        if c < min_count:
            continue
        ranked.append((w, c))
    return dedupe_ngrams(ranked)[:top_n]


def imbalance_warning(rows, mapping, threshold=3):
    """一树吊死检查：某个现有文献综述主题下只挂 1 篇，而另一主题挂 ≥threshold 篇。

    这是主题聚类失败的信号——正确聚类应让各主题文献数大致均衡。
    """
    counts = []
    for row in rows:
        value = (row.get("spl") or "").strip()
        if value:
            counts.append((value[:40], 1))

    if len(counts) < 2:
        return []

    freq = Counter(name for name, _ in counts)
    singles = [n for n, c in freq.items() if c == 1]
    heavy = [n for n, c in freq.items() if c >= threshold]
    if singles and heavy:
        return [
            "主题聚类可能失衡：%d 个现有文献综述主题仅挂 1 篇，"
            "而 %d 个主题挂 ≥%d 篇。请回 RCOS 重新按主题聚类，"
            "避免『洗衣店接衣单』式罗列。"
            % (len(singles), len(heavy), threshold)
        ]
    return []


# ---------------------------------------------------------------- 主流程

def load_rows(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        if not reader.fieldnames:
            raise ValueError("CSV 无表头行")
        mapping, unknown = resolve_columns(reader.fieldnames)
        rows = []
        for raw in reader:
            row = {}
            for canon, col in mapping.items():
                row[canon] = (raw.get(col) or "").strip()
            rows.append(row)
    return rows, mapping, unknown


# 内部规范名 -> 输出用的中文密码名（与 review 文档保持一致）
FIELD_LABELS = {
    "spl": "现有文献综述",
    "cpl": "现有文献批评",
    "gap": "空白",
    "rof": "研究结果",
    "rfw": "未来研究建议",
    "poc": "批评点",
    "mop": "明显的遗漏点",
    "rpp": "待探讨的相关问题",
    "author": "作者",
    "year": "年份",
    "title": "标题",
    "source": "来源",
    "no": "序号",
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

    # 列映射
    missing_cols = [f for f in REQUIRED if f not in mapping]
    if missing_cols:
        problems.append("缺少必需列: %s（可接受表头见 FIELD_ALIASES）"
                        % ", ".join(label(f) for f in missing_cols))
    if unknown:
        warnings.append("未识别的列（将忽略）: %s" % ", ".join(unknown))
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
            "%d/%d 篇缺少必需栏（%s）。缺「研究结果」无法做主题聚类，"
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
    for msg in imbalance_warning(rows, mapping):
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


def print_cluster(rows):
    print()
    print("=" * 60)
    print("主题聚类提示（来自「研究结果 / 现有文献综述 / 现有文献批评 / 空白」四栏）")
    print("=" * 60)
    ranked = cluster_hint(rows)
    if not ranked:
        print("（无可用词频——四栏可能均为空）")
        return
    print("候选主题词（按复现度排序）:")
    for word, count in ranked:
        print("  %-14s %2d" % (word, count))

    distinct = len(ranked)
    print()
    print("→ 目标：压缩成 8–10 个「现有文献综述」主题（形态 B 提纲第 1 层）")
    print("→ 目标：归纳成 3–5 组「现有文献批评 / 空白」主题（提纲第 2 层）")
    if distinct < 8:
        print("⚠  候选词偏少（%d 个），可能「研究结果 / 现有文献综述」填写过于笼统。"
              % distinct)
        print("   建议：这两栏应写具体发现与具体主题，"
              "而非『分析了XX研究』这类空泛表述。")


def cmd_init(path):
    header = ["序号", "作者", "年份", "标题", "来源", "现有文献综述", "现有文献批评",
          "空白", "研究结果", "未来研究建议", "批评点/待探讨问题"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow(header)
    print("已生成 RCOS 模板: %s" % path)
    print("填写要点：读完当天录入；「现有文献综述」「研究结果」两栏"
          "写具体内容，不写空泛表述。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="RCOS 校验与主题聚类提示")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_rcos = sub.add_parser("rcos", help="校验 RCOS 并给出聚类提示")
    p_rcos.add_argument("csv_path", help="RCOS CSV 路径")
    p_rcos.add_argument("--check-only", action="store_true", help="只做完备性检查")

    p_init = sub.add_parser("init", help="生成空白 RCOS 模板")
    p_init.add_argument("output", help="输出 CSV 路径")

    args = parser.parse_args()

    if args.cmd == "init":
        return cmd_init(args.output)

    if not os.path.exists(args.csv_path):
        print("文件不存在: %s" % args.csv_path, file=sys.stderr)
        return 1

    rows, _mapping, problems, warnings = check(args.csv_path)

    if not args.check_only and rows:
        print_cluster(rows)

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
