#!/usr/bin/env python3
"""形态 D 的语料登记与增量同步：判重、编号、状态查询。

为什么需要它：文献量一大，「哪篇读过、哪篇只是躺在那儿」就会失真。
本脚本把「发现 → 去重 → 抽文本 → 分流 → 解码 → 入 RCOS → 入综述」这条链
落成一张 CSV，并保证**后期新增文献时只追加、不重算**。

用法：
    # 建空登记表
    python3 sync_corpus.py init 01_文献总表/文献总表.csv

    # 扫描来源目录/文件，只追加新内容（按 sha1 判重）
    python3 sync_corpus.py scan 01_文献总表/文献总表.csv --source 新增文献/ --source /abs/x.pdf

    # 看进度与待办
    python3 sync_corpus.py status 01_文献总表/文献总表.csv

退出码：
    0  正常（无新增）
    1  输入或表结构错误
    3  本次追加了新记录（便于串联「有新增才继续抽文本」的流水线）

设计约束（改动前请先读）：
- **身份字段只写一次**：编号／文件名／sha1／源路径。重跑扫描不得改写它们。
- **判断字段绝不被脚本覆盖**：档位／纳入判定／主题分类／相关度／解码状态／产出文件／备注，
  只在**新行**上给初值；已有行一律原样保留——那是人（或读文队友）的判断。
- 判重按内容（sha1），不按文件名：文件名会变（`_副本`、` (1)`、重命名），内容不会。
"""

import argparse
import csv
import hashlib
import os
import re
import sys

# ------------------------------------------------------------------ 常量

REGISTRY_FIELDS = [
    "编号", "文件名", "标题", "第一作者", "年份", "来源类型",
    "页数", "字符数", "sha1", "源路径", "文本路径",
    "主题分类", "档位", "纳入判定", "相关度", "解码状态", "产出文件", "备注",
]

# 脚本只负责给新行填这几栏的初值，其余留空待人或其它脚本回填
NEW_ROW_DEFAULTS = {
    "主题分类": "",
    "档位": "待分流",
    "纳入判定": "待核查",
    "相关度": "",
    "解码状态": "未处理",
    "产出文件": "",
}

# 表头容错：允许旧表用别名，读入时归一到规范名
HEADER_ALIASES = {
    "序号": "编号", "no": "编号", "id": "编号",
    "作者": "第一作者", "第一作者": "第一作者",
    "题名": "标题", "题目": "标题", "篇名": "标题",
    "路径": "源路径", "文件路径": "源路径", "绝对路径": "源路径",
    "txt路径": "文本路径", "提取路径": "文本路径",
    "主题": "主题分类", "分类": "主题分类",
    "判定": "纳入判定", "相关性": "相关度", "相关度": "相关度",
    "状态": "解码状态", "处理状态": "解码状态",
    "产出": "产出文件", "输出文件": "产出文件",
}

DOC_EXTS = {".pdf", ".docx", ".doc", ".md", ".txt", ".pptx", ".xlsx"}
IMG_EXTS = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
DEFAULT_EXTS = DOC_EXTS | IMG_EXTS

POLICY_MARKERS = ("通知", "指南", "标准", "指导纲要", "纲要", "课程方案",
                  "课程标准", "红皮书", "白皮书", "决定", "意见", "办法")
THESIS_MARKERS = ("硕士学位论文", "博士学位论文", "学位论文", "博士论文", "硕士论文")
BOOK_MARKERS = ("专著", "红皮书", "蓝皮书", "年鉴")

# 文件名里的重复标记：`_副本`、` (1)`、`（2）`、`- 副本`、` 2`
#
# 两个坑必须绕开（2026-10-07 实测踩过）：
# ① 裸数字后缀不能允许**零宽**分隔符，否则 "s40561-025-00413-1" 会被逐段剥成
#    "s40561"、"s"——两个毫无关系的 DOI 名会归一成同一个字符串，报出假「同名」；
# ② 剥后缀要反复循环（`xxx_副本 (1)` 有两层），但每层都必须真的消耗掉分隔符。
_DUP_SUFFIX = re.compile(
    r"(?:[ _\-]+(?:副本|拷贝|复件|copy(?:\s*\d+)?)"
    r"|[ _\-]*[（(]\d+[）)]"
    r"|[ _\-]+\d+)\s*$",
    re.IGNORECASE,
)


# ------------------------------------------------------------------ 小工具

def normalize_title(name: str) -> str:
    """把文件名（或标题）归一到可比较的形态，用于「同名」判定。

    只做与判重有关的归一：去扩展名、去重复标记、全角括注转半角、去空白。
    **不**做同义词归并——那是人的判断，脚本不越界。
    """
    stem = os.path.splitext(os.path.basename(name))[0]
    stem = stem.replace("（", "(").replace("）", ")")
    # 最多剥两层：`xxx_副本 (1)` 需要两层，再多就可能把 ID 型文件名
    # （如 s40561-025-00413-1）一路剥成公共前缀，报出假「同名」。
    for _ in range(2):
        stripped = _DUP_SUFFIX.sub("", stem).strip()
        if stripped == stem:
            break
        stem = stripped
    stem = re.sub(r"\s+", "", stem)
    return stem.lower()


def parse_author_title(filename: str):
    """从 `标题_作者.pdf` 形态的文件名解析 (标题, 作者)。

    CNKI 批量下载的命名几乎都是这个形态；解析不出来时返回 (原名, "")——
    **宁可留空让人工补，也不要猜**。
    """
    stem = os.path.splitext(os.path.basename(filename))[0]
    stem = stem.replace("（", "(").replace("）", ")")
    for _ in range(2):                       # 与 normalize_title 同规则，见其注释
        stripped = _DUP_SUFFIX.sub("", stem).strip()
        if stripped == stem:
            break
        stem = stripped
    if "_" not in stem:
        return stem, ""
    title, _, author = stem.rpartition("_")
    # 作者段过长（多半是标题里本来就带下划线）时保守放弃：宁可留空让人工补，
    # 也不要把标题的尾巴当成作者——错的作者名会污染整张表的检索与聚类。
    if not title or not author or len(author) > 4 or " " in author.strip():
        return stem, ""
    return title.strip(), author.strip()


def guess_source_type(filename: str) -> str:
    """按文件名给来源类型一个**初判**；不确定就写「待核」而不是硬猜。

    ⚠️ **内容标记优先于扩展名**。旧实现把 `.docx/.doc → 文档` 放在最前，
    于是"通知""指南""学位论文"这些标记根本没机会生效
    （2026-10-08 实测：本课题 4 份政策 .docx 全被判为「文档」，
    而文件名里明明写着「通知」「指南」）。
    来源类型是四档分流（T1/T2/T3）的输入，判错会让政策文件按普通文档处理。
    """
    ext = os.path.splitext(filename)[1].lower()
    if any(m in filename for m in THESIS_MARKERS):
        return "学位论文"
    if any(m in filename for m in POLICY_MARKERS):
        return "政策文件"
    if any(m in filename for m in BOOK_MARKERS):
        return "专著"
    if ext in IMG_EXTS:
        return "图片"
    if ext in {".docx", ".doc"}:
        return "文档"
    if ext == ".md":
        return "笔记"
    if ext == ".pdf":
        return "期刊论文"      # 待核：多数 PDF 是期刊论文，但仍需读后确认
    return "待核"


def sha1_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _resolve_registry_field(name: str) -> str:
    """把表头/字段名解析为登记表的规范字段名（先查别名表）。

    ⚠️ 与 `build_review.norm_header()` **不是同一个操作**，别混用：
      - `build_review.norm_header`：只做通用归一（小写 + 去空格标点），用于匹配 RCOS 列；
      - 本函数：在归一前先查 `HEADER_ALIASES` 别名，返回的是**字段名**而非匹配键。
    历史上两者同名（都叫 norm_header），读代码时极易串。
    """
    key = name.strip().replace(" ", "").replace("\u3000", "")
    if key in REGISTRY_FIELDS:
        return key
    return HEADER_ALIASES.get(key, key)


def load_registry(path: str):
    """读登记表 → (字段列表, 行列表)。缺列自动补齐，不丢已有数据。"""
    if not os.path.exists(path):
        raise FileNotFoundError(f"登记表不存在：{path}（先用 init 建表）")
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        raw_fields = reader.fieldnames or []
        fields = [_resolve_registry_field(f) for f in raw_fields]
        rows = []
        for raw in reader:
            row = {}
            for src, dst in zip(raw_fields, fields):
                row[dst] = (raw.get(src) or "").strip()
            for f in REGISTRY_FIELDS:
                row.setdefault(f, "")
            rows.append(row)
    missing = [f for f in REGISTRY_FIELDS if f not in fields]
    if missing:
        # 旧表缺列：补在末尾，不重排既有列，避免破坏人工维护的列序
        fields = fields + missing
    return fields, rows


def save_registry(path: str, fields: list, rows: list) -> None:
    """原子写：先写临时文件再 replace，避免中断留下半张表。"""
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({f: row.get(f, "") for f in fields})
    os.replace(tmp, path)


def _next_index(rows: list, prefix: str = "L") -> int:
    top = 0
    for row in rows:
        m = re.fullmatch(re.escape(prefix) + r"(\d+)", row.get("编号", ""))
        if m:
            top = max(top, int(m.group(1)))
    return top + 1


def _append_note(row: dict, note: str) -> None:
    old = row.get("备注", "")
    row["备注"] = f"{old}；{note}" if old and note not in old else (old or note)


# ------------------------------------------------------------------ 子命令

def cmd_init(args) -> int:
    path = args.registry
    if os.path.exists(path) and not args.force:
        print(f"已存在，未覆盖：{path}（要重建请加 --force）", file=sys.stderr)
        return 1
    save_registry(path, REGISTRY_FIELDS, [])
    print(f"已建空登记表：{path}（{len(REGISTRY_FIELDS)} 栏）")
    return 0


def _collect_targets(sources, exts):
    targets = []
    for src in sources:
        if os.path.isfile(src):
            if os.path.splitext(src)[1].lower() in exts:
                targets.append(os.path.abspath(src))
            continue
        if not os.path.isdir(src):
            print(f"⚠️  来源不存在，已跳过：{src}", file=sys.stderr)
            continue
        for root, dirs, files in os.walk(src):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for name in sorted(files):
                if name.startswith(".") or name.startswith("~$"):
                    continue
                if os.path.splitext(name)[1].lower() in exts:
                    targets.append(os.path.abspath(os.path.join(root, name)))
    return sorted(set(targets))


def cmd_scan(args) -> int:
    try:
        fields, rows = load_registry(args.registry)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    exts = {e if e.startswith(".") else "." + e for e in args.ext} if args.ext \
        else set(DEFAULT_EXTS)
    targets = _collect_targets(args.source, exts)
    if not targets:
        print("没有找到可处理的文件（检查 --source 与 --ext）", file=sys.stderr)
        return 1

    by_sha = {}
    by_title = {}
    by_path = set()
    for row in rows:
        if row.get("sha1"):
            by_sha.setdefault(row["sha1"], row)
        if row.get("源路径"):
            by_path.add(os.path.abspath(row["源路径"]))
        if row.get("标题") or row.get("文件名"):
            by_title.setdefault(normalize_title(row.get("标题") or row["文件名"]), row)

    added, skipped_dup, flagged = [], 0, []
    idx = _next_index(rows)
    for path in targets:
        # 已在表里的**同一路径**直接跳过。不能只看 sha1：开了
        # --record-duplicates 时，重复文件会每扫一次多出一行「重复记录」，
        # 反复同步后登记表会被自己的重复行淹掉。
        if path in by_path:
            continue
        digest = sha1_file(path)
        name = os.path.basename(path)
        if digest in by_sha:
            skipped_dup += 1
            if args.record_duplicates:
                dup = {f: "" for f in REGISTRY_FIELDS}
                dup.update({
                    "编号": f"L{idx:04d}", "文件名": name,
                    "标题": parse_author_title(name)[0],
                    "第一作者": parse_author_title(name)[1],
                    "来源类型": guess_source_type(name), "sha1": digest,
                    "源路径": path, "档位": "去重-重复", "纳入判定": "去重",
                    "解码状态": "跳过",
                    "备注": f"与 {by_sha[digest].get('编号')}（{by_sha[digest].get('文件名')}）内容相同",
                })
                rows.append(dup)
                added.append(dup)
                idx += 1
            continue

        title, author = parse_author_title(name)
        row = {f: "" for f in REGISTRY_FIELDS}
        row.update({
            "编号": f"L{idx:04d}", "文件名": name, "标题": title,
            "第一作者": author, "来源类型": guess_source_type(name),
            "sha1": digest, "源路径": path,
        })
        row.update(NEW_ROW_DEFAULTS)

        norm = normalize_title(title)
        if norm in by_title:
            other = by_title[norm]
            row["纳入判定"] = "待核查"
            # 这里只判「文件名归一后相同」，**绝不等于内容不同**：同一篇重新下载/重新保存
            # 就会字节不同。2026-10-07 实测踩过：把这句写成"同名不同内容"，五对逐字相同的
            # 重复文献被当成不同文献统计，读队列虚增。结论必须由文本层比对给出。
            _append_note(row, f"与 {other.get('编号')}（{other.get('文件名')}）文件名归一后同名："
                              "可能为重复下载／重新保存，须比对抽取文本层（batch_extract 会给结论）后定取舍")
            flagged.append(row)
        rows.append(row)
        added.append(row)
        by_sha[digest] = row
        by_title.setdefault(norm, row)
        idx += 1

    if added and not args.dry_run:
        save_registry(args.registry, fields, rows)

    print(f"扫描来源 {len(targets)} 个文件｜新增 {len(added)} 条"
          f"（其中同名不同内容 {len(flagged)} 条）｜内容重复跳过 {skipped_dup} 条")
    for row in added[:20]:
        print(f"  + {row['编号']}  {row['文件名']}")
    if len(added) > 20:
        print(f"  … 另有 {len(added) - 20} 条")
    if flagged:
        print("⚠️  同名不同内容（最易静默覆盖，务必人工核对）：")
        for row in flagged:
            print(f"  ! {row['编号']}  {row['文件名']}")
    if args.dry_run:
        print("（--dry-run：未写入登记表）")
    if args.report and not args.dry_run:
        _write_scan_report(args.report, targets, added, skipped_dup, flagged)
        print(f"报告已写入 {args.report}")
    return 3 if added else 0


def _write_scan_report(path, targets, added, skipped_dup, flagged):
    lines = ["# 文献同步报告", "",
             f"- 扫描文件：{len(targets)}",
             f"- 新增记录：{len(added)}",
             f"- 内容重复跳过：{skipped_dup}",
             f"- 同名不同内容（需人工核对）：{len(flagged)}", ""]
    if added:
        lines += ["## 新增记录", "", "| 编号 | 文件名 | 初判类型 | 备注 |", "|---|---|---|---|"]
        lines += [f"| {r['编号']} | {r['文件名']} | {r['来源类型']} | {r['备注']} |" for r in added]
        lines += ["", "> 下一步：按 `references/batch-workflow.md` 第 2 节给新行分档，"
                      "再运行 `batch_extract.py` 抽文本。旧行不动。", ""]
    if flagged:
        lines += ["## 同名不同内容", ""] + \
                 [f"- {r['编号']} {r['文件名']}：{r['备注']}" for r in flagged] + [""]
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def cmd_attach(args) -> int:
    """把外部抽好的文本挂到某一编号上——**OCR 回流的正式入口**。

    为什么需要它：状态机里「需OCR」的那条线写着"先 OCR 再回主流程"，但
    **回流没有任何工具**——抽完的文本只能手工去改登记表的 `文本路径` 与
    `解码状态`（2026-10-08 实测：sync_corpus 只有 init/scan/status 三个子命令，
    batch_extract 里 `已OCR` 只作为"受保护状态"被读取）。手工改表既容易写错，
    也绕过了"身份字段不得改"的约束。

    本命令只写**脚本回填字段**（文本路径／字符数／解码状态），
    **绝不碰** 档位／纳入判定／主题分类／相关度——那四项是人的判断。
    """
    fields, rows = load_registry(args.registry)
    target = None
    for row in rows:
        if row.get("编号") == args.id or row.get("文件名") == args.id:
            target = row
            break
    if target is None:
        print(f"找不到编号或文件名：{args.id}", file=sys.stderr)
        return 1
    if not os.path.exists(args.text):
        print(f"文本文件不存在：{args.text}", file=sys.stderr)
        return 1
    # 与 batch_extract 的抽查保持一致的"有没有内容"判据
    with open(args.text, encoding="utf-8", errors="replace") as fh:
        content = fh.read()
    chars = len(re.sub(r"\s", "", content))
    if chars < 10:
        print(f"文本几乎为空（{chars} 字）——空文本会被下游误读成「原文没写」，"
              "拒绝挂载。", file=sys.stderr)
        return 1

    before = target.get("解码状态") or "（空）"
    target["文本路径"] = os.path.abspath(args.text)
    target["字符数"] = str(chars)
    target["解码状态"] = "已OCR"
    note = args.note or "文本由外部 OCR 挂载"
    if note not in (target.get("备注") or ""):
        target["备注"] = ((target.get("备注") or "") + "；" + note).strip("；")
    save_registry(args.registry, fields, rows)
    print(f"已挂载：{target['编号']}  {target['文件名']}")
    print(f"  解码状态：{before} → 已OCR｜字符数：{chars}｜文本：{target['文本路径']}")
    print("  提醒：已OCR 状态受保护，重抽需显式 --redo-ocr（防 OCR 成果被覆盖）。")
    return 0


def cmd_status(args) -> int:
    try:
        fields, rows = load_registry(args.registry)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    if not rows:
        print("登记表里没有任何文献行（只有表头）。先运行 scan。", file=sys.stderr)
        return 1

    def tally(field):
        counts = {}
        for row in rows:
            counts[row.get(field) or "（空）"] = counts.get(row.get(field) or "（空）", 0) + 1
        return sorted(counts.items(), key=lambda kv: -kv[1])

    print(f"共 {len(rows)} 行")
    for field in ("档位", "纳入判定", "解码状态", "主题分类", "来源类型"):
        print(f"\n【{field}】")
        for key, num in tally(field):
            print(f"  {key:16s} {num:4d}")

    problems = []
    for row in rows:
        status, out, tier = row.get("解码状态", ""), row.get("产出文件", ""), row.get("档位", "")
        # ★已定档为核心/重要、但还没走到「已解码」的，全部算待办。
        #   旧写法只查 `status in ("", "未处理")`——一旦抽出文本，状态变成「已抽文本」，
        #   这条检查就不再触发，于是"定档了却一直没出解码卡"可以**毫无提示地停在原地**。
        #   2026-10-08 实测：15 篇 T1/T2 全部停在「已抽文本」，而 status 报「待办／异常 0 条」。
        #   这是最容易发生的静默停滞——流程看起来在跑，其实一步没动。
        DONE = ("已解码", "已入RCOS", "已入综述")
        if tier in ("T1核心", "T2重要", "专著章节") and status not in DONE:
            problems.append(f"{row['编号']} 已定档 {tier} 但尚未解码"
                            f"（状态={status or '未处理'}）：{row['文件名']}")
        if status == "已解码" and not out:
            problems.append(f"{row['编号']} 标记已解码但未填产出文件：{row['文件名']}")
        if status == "跳过" and "重复" not in tier and "重复" not in row.get("备注", ""):
            problems.append(f"{row['编号']} 状态为跳过但未说明理由：{row['文件名']}")
        # 反向检查：去重行必须保持「跳过」。曾经 batch_extract 会把去重行照抽一遍
        # 并把状态覆盖成「已抽文本」，使它自相矛盾（档位说重复、状态说已抽），
        # 同时产出两份 sha1 相同的文本——已修，这条检查用于防止回归。
        if "重复" in tier and status != "跳过":
            problems.append(f"{row['编号']} 档位为「{tier}」但状态是「{status or '未处理'}」："
                            f"去重决定未落实或被覆盖，须复核：{row['文件名']}")
        if row.get("纳入判定") == "待核查":
            problems.append(f"{row['编号']} 待核查：{row['文件名']}（{row.get('备注') or '无备注'}）")
    print(f"\n【待办／异常】{len(problems)} 条")
    for item in problems[:40]:
        print(f"  - {item}")
    if len(problems) > 40:
        print(f"  … 另有 {len(problems) - 40} 条")
    return 0


# ------------------------------------------------------------------ 入口

def build_parser():
    p = argparse.ArgumentParser(description="文献登记表：判重、增量同步、状态查询")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init", help="建空登记表")
    p_init.add_argument("registry")
    p_init.add_argument("--force", action="store_true", help="已存在时覆盖重建")
    p_init.set_defaults(func=cmd_init)

    p_scan = sub.add_parser("scan", help="扫描来源，只追加新内容")
    p_scan.add_argument("registry")
    p_scan.add_argument("--source", action="append", required=True,
                        help="文件或目录，可重复")
    p_scan.add_argument("--ext", action="append", default=None,
                        help=f"按扩展名过滤，可重复（默认 {sorted(DEFAULT_EXTS)}）")
    p_scan.add_argument("--record-duplicates", action="store_true",
                        help="把内容重复的文件也登记为一行（档位=去重-重复）")
    p_scan.add_argument("--dry-run", action="store_true", help="只报不改")
    p_scan.add_argument("--report", help="把同步报告写成 Markdown")
    p_scan.set_defaults(func=cmd_scan)

    p_status = sub.add_parser("status", help="看进度与待办")
    p_status.add_argument("registry")
    p_status.set_defaults(func=cmd_status)

    p_attach = sub.add_parser(
        "attach", help="把外部抽好的文本挂到某一编号（OCR 回流的正式入口）")
    p_attach.add_argument("registry")
    p_attach.add_argument("id", help="编号（如 L0007）或完整文件名")
    p_attach.add_argument("--text", required=True, help="要挂载的文本文件")
    p_attach.add_argument("--note", help="追加到备注的说明")
    p_attach.set_defaults(func=cmd_attach)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
