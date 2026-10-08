#!/usr/bin/env python3
"""按登记表批量抽文本：可中断、可重跑、只处理没抽过的。

为什么单独一个脚本：形态 D 的语料动辄上百份，逐篇手敲
`extract_pdf_text.py` 会漏、会重、会把「没抽到」当成「原文没写」。
本脚本把状态回写登记表，让「抽了没有」变成可查的事实。

用法：
    python3 batch_extract.py --registry 01_文献总表/文献总表.csv --outdir 02_文本
    python3 batch_extract.py --registry ... --outdir ... --force        # 全部重抽
    python3 batch_extract.py --registry ... --outdir ... --limit 10     # 先试 10 篇
    python3 batch_extract.py --registry ... --outdir ... --dry-run

行为约定：
- 只处理 **没有文本路径 / 文本文件已丢失 / --force** 的行；已抽的一律跳过（增量友好）。
- 每篇写 `<编号>_<安全标题>.txt`，正文用 `===== PAGE n =====` 标记，保留回指能力。
- 多数页提不出文本 → 解码状态 `需OCR`，**绝不**留下空文件冒充「已抽文本」。
- 非 PDF（docx/png/md）登记为 `待其它提取器` 或 `需OCR`，由人决定用哪个工具。
- 登记表用原子写，且**只回填** 页数／字符数／文本路径／解码状态，不动判断字段。
- 抽完做一次**文本层版本重复**检查（`--no-dup-check` 可关）：同一篇文献被重新保存后
  文件字节会变、文件级 sha1 判不出来，但抽取文本完全一致——不查就会对同一篇读两遍、
  写出两份互相矛盾的解码卡。脚本只报告，取舍由人定。

退出码：0 正常；1 输入/表结构错误；2 有文件需要 OCR（OCR 队列非空）。
"""

import argparse
import hashlib
import importlib.util
import os
import re
import sys

# 与 sync_corpus 共用登记表结构，避免两边各定义一份而漂移
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

try:
    from sync_corpus import REGISTRY_FIELDS, load_registry, save_registry
except ImportError:                                   # 退路：允许单独拷走使用
    REGISTRY_FIELDS = ["编号", "文件名", "标题", "第一作者", "年份", "来源类型",
                       "页数", "字符数", "sha1", "源路径", "文本路径", "主题分类",
                       "档位", "纳入判定", "相关度", "解码状态", "产出文件", "备注"]
    load_registry = save_registry = None

# 允许中文、字母数字、下划线、点、连字符与加号（"AI+工程"这类标题要保留原样）
_SAFE = re.compile(r"[^\w\u4e00-\u9fff.+\-]+")
# 「需OCR」判据的阈值不在此处定义——唯一来源是 extract_pdf_text.SCANNED_PAGE_MIN_CHARS，
# 由 extract_one() 通过传入的 extractor 读取（历史上这里另有一份同名常量，
# 两处各自演化会导致单篇与批量对「扫描件」的判断不一致）。

# 文件名里的重复标记，用于在同文本的多份文件里挑「最干净」的那份
# 「副本标记」的判定。⚠️ sync_corpus.py 另有一份 `_DUP_SUFFIX`（带 $ 锚定、用于剥后缀求同名键）。
# 两者职责不同（本处只**计数**用于同题多份时排序取原件，不删任何行），但同属「副本标记」这一概念；
# 若日后要扩词（如「终版」「v2」），**两处都要改**，否则两个脚本会对同一文件给出不同的重复判断。
_DUP_MARK = re.compile(r"副本|拷贝|复件|copy|[（(]\d+[）)]|[ _\-]\d+$", re.IGNORECASE)


def _dup_mark_count(name: str) -> int:
    return len(_DUP_MARK.findall(name))


def find_text_duplicates(rows):
    """按**抽取文本的内容哈希**找版本重复 → [(保留行, 重复行), …]。

    为什么不能只看文件 sha1：同一篇文献重新保存、换个页眉或元数据，字节就变了，
    文件级 sha1 判不出来，于是会在登记表里变成两条互不相干的记录，
    下游就会对同一篇文献读两遍、写出两份互相矛盾的解码卡。

    只做**报告**，不改判断字段（档位/纳入判定/解码状态都是人的判断，
    脚本越权去改会让「谁做的决定」无法追溯）。
    """
    groups = {}
    for row in rows:
        path = row.get("文本路径", "")
        if not path or not os.path.exists(path):
            continue
        try:
            with open(path, "rb") as fh:
                digest = hashlib.sha1(fh.read()).hexdigest()
        except OSError:
            continue
        groups.setdefault(digest, []).append(row)

    pairs = []
    for items in groups.values():
        if len(items) < 2:
            continue
        # 保留文件名最干净（重复标记最少）、编号最小的那份
        items = sorted(items, key=lambda r: (_dup_mark_count(r["文件名"]), r["编号"]))
        for dup in items[1:]:
            pairs.append((items[0], dup))
    return pairs


def _load_extractor():
    """加载同目录的 extract_pdf_text，复用它已打磨好的清洗逻辑。

    不复用就等于把「页码识别只在首尾、断词只在字母后接回」这些
    踩过坑的规则重写一遍，迟早写歪。
    """
    try:
        import extract_pdf_text as module
        return module
    except ImportError:
        path = os.path.join(_HERE, "extract_pdf_text.py")
        spec = importlib.util.spec_from_file_location("extract_pdf_text", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


def is_dedup_row(row) -> bool:
    """该行是否已被 `sync_corpus` 判为重复——即"不要处理这一行"的决定。

    三个字段任一命中即算：档位含「重复」（去重-重复）、纳入判定＝去重、状态＝跳过。
    """
    return ("重复" in (row.get("档位") or "")
            or (row.get("纳入判定") or "") == "去重"
            or (row.get("解码状态") or "") == "跳过")


def safe_filename(text: str, limit: int = 60) -> str:
    text = _SAFE.sub("_", (text or "").strip()) or "untitled"
    return text[:limit].strip("._-") or "untitled"


def detect_printed_offset(pages_raw):
    """推断「印刷页码 − PDF 序」的恒定偏移；推不出就返回 None。

    为什么必须做这件事（2026-10-07 独立校验发现的系统性问题）：
    提取标记 `===== PAGE n =====` 里的 n 是 **PDF 序**，而学术写作引用的是**印刷页码**。
    CNKI 期刊 PDF 前面常有目录页/版权页，学位论文有封面与摘要，
    于是一篇文献的 p.14 在提取文本里是 PAGE 1，另一篇的 p.40 是 PAGE 1——
    **偏移量每篇不同（实测 +13／+37／−8／+39），无法统一换算**。
    结果是 86 份产出全部通过格式自检，却没有一条引用能被机械核对。

    ⚠️ **默认不启用标注（--printed-labels 才启用）**。原因（2026-10-07 独立校验实测）：
    CNKI 页脚的两位页码常被抽成**相邻两个单数字记号**（页脚 `80` → 记号 `0`、`8`），
    本函数只取其一，于是整篇偏移差 10 的倍数——6 份文档的标注页码全错，
    而读文人按原 PDF 写的页码反而是对的。**内部一致性（偏移恒定、页序连续）查不出这类错**，
    必须用外部信号对撞（页内「（下转第 N 页）」、期号、与产出引用比对）才能发现。
    因此：**没有外部验证就不要标注**。

    做法：页码总是单独成行且靠近页首或页尾，与 PDF 序保持恒定差值。
    取出现次数最多的差值，且须覆盖至少一半页数才采信（避免把表格里的裸数字当页码）。

    ⚠️ 必须看**页首/页尾各若干行**，不能只看第一行：CNKI 期刊页首常有
    「FOCUS／本期策划」这类栏目眉（实测清华大中小学一文，印刷页码在第 3 行），
    只看首行会整体漏检——漏检后所有引用印刷页码的产出都无法被机械核对。
    """
    window = 5
    candidates = []
    for idx, raw in enumerate(pages_raw):
        lines = [ln.strip() for ln in raw.replace("\r\n", "\n").split("\n") if ln.strip()]
        if not lines:
            continue
        edge = lines[:window] + (lines[-window:] if len(lines) > window else [])
        for line in edge:
            if re.fullmatch(r"\d{1,4}", line):
                candidates.append(int(line) - (idx + 1))
    if not candidates:
        return None
    counts = {}
    for value in candidates:
        counts[value] = counts.get(value, 0) + 1
    offset, hits = max(counts.items(), key=lambda kv: kv[1])
    if hits < max(2, 0.5 * len(pages_raw)):
        return None
    return offset


def extract_one(pdf_path: str, out_path: str, extractor, with_printed_labels: bool = False):
    """抽一篇 → (状态, 页数, 字符数, 提示消息)。

    状态取值：ok / 需OCR / 打不开；字符数是**非空白字符**数，
    因为版面噪声会让 len(text) 虚高，用它判断「有没有内容」会误判。

    每个 `===== PAGE n =====` 标记后追加 `[印刷页码 p.x]`（推得出偏移时），
    使引用**印刷页码**与引用**标记页**两种写法都能被回原文核对。
    """
    try:
        import pymupdf as fitz
    except ImportError:
        try:
            import fitz
        except ImportError:
            return "缺少依赖", 0, 0, "需要 PyMuPDF：pip install pymupdf"

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        return "打不开", 0, 0, f"无法打开 PDF：{exc}"

    try:
        total = doc.page_count
        pages_raw = [doc[i].get_text() for i in range(total)]
    finally:
        doc.close()

    page_number_lines = extractor.detect_page_number_lines(pages_raw)
    offset = detect_printed_offset(pages_raw)
    chunks, empty_pages = [], []
    for offset_idx, raw in enumerate(pages_raw):
        drop_positions = {pos for idx, pos in page_number_lines if idx == offset_idx}
        cleaned = extractor.clean_page_text(raw, drop_positions=drop_positions)
        if len(re.sub(r"\s", "", cleaned)) < extractor.SCANNED_PAGE_MIN_CHARS:
            empty_pages.append(offset_idx + 1)
        label = extractor.MARKER.format(offset_idx + 1)
        if offset is not None and with_printed_labels:
            printed = offset_idx + 1 + offset
            # 只有印刷页码 ≥1 才标注：学位论文前有封面/摘要/目录，
            # 依偏移往前推会得出 p.-7 这种不可能的页码，标出来比不标更坏。
            if printed >= 1:
                label += f" [印刷页码 p.{printed}]"
        chunks.append(f"{label}\n{cleaned}\n")

    content = "".join(chunks)
    chars = len(re.sub(r"\s", "", content))
    scanned = bool(empty_pages) and len(empty_pages) >= max(1, 0.5 * total)

    if scanned:
        # 不写文件：写了空壳，下游极易把「没抽到」读成「原文没写」
        return "需OCR", total, chars, f"{len(empty_pages)}/{total} 页无文本层，需先 OCR"

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return "ok", total, chars, ""


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="按登记表批量抽文本")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--outdir", required=True)
    parser.add_argument("--force", action="store_true", help="已抽过的也重抽")
    parser.add_argument("--limit", type=int, default=0, help="最多处理几篇（0=不限）")
    parser.add_argument("--dry-run", action="store_true", help="只列计划，不写文件")
    parser.add_argument("--redo-ocr", action="store_true",
                        help="允许对已 OCR 的图片型文献重新抽文本（默认保护其 OCR 结果）")
    parser.add_argument("--printed-labels", action="store_true",
                        help="在页码标记后追加推断的印刷页码。**默认关闭**："
                             "CNKI 页码常被抽成相邻两个单数字记号（页脚 80 → 记号 0、8），"
                             "只取其一会让整篇偏移差 10 的倍数，标注错误比不标更坏——"
                             "启用前必须用外部信号（页内「下转第 N 页」、期号、与原文对撞）验证")
    parser.add_argument("--no-dup-check", action="store_true",
                        help="跳过「抽取文本一致但文件字节不同」的版本重复检查")
    parser.add_argument("--report", default="", help="提取报告路径（默认 <outdir>/_提取报告.md）")
    args = parser.parse_args(argv)

    if load_registry is None:
        print("缺少 sync_corpus.py（需与本脚本同目录）", file=sys.stderr)
        return 1
    try:
        fields, rows = load_registry(args.registry)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1

    extractor = _load_extractor()
    os.makedirs(args.outdir, exist_ok=True)
    dup_skipped = 0

    todo = []
    for row in rows:
        src = row.get("源路径", "")
        if not src or not os.path.exists(src):
            if src:
                row["解码状态"] = "源文件缺失"
            continue
        ext = os.path.splitext(src)[1].lower()
        if ext != ".pdf":
            if row.get("解码状态") in ("", "未处理"):
                row["解码状态"] = "待其它提取器"
                row["备注"] = (row.get("备注", "") + "；非 PDF，需 docx/OCR 等提取器").strip("；")
            continue
        have = row.get("文本路径", "")
        # 已是 OCR 结果的图片型文献必须保护：重抽只可能再得"需OCR"，
        # 却会把 文本路径 清空、状态打回"需OCR"——静默回退（2026-10-07 实测踩过：
        # 一次 --force 重抽把两篇 OCR 过的文献打回原点，直到盘点才发现）。
        # ★OCR 行是否放行**只由 --redo-ocr 决定**；非 OCR 行才受 --force 影响。
        #   旧实现是两个平列的 continue：
        #     ① 已OCR + 有文本 + 未给 --redo-ocr → 跳过（保护；该条不看 --force，
        #        故 --force 一直**没能**破坏 OCR 结果，既有测试 test_force_does_not_
        #        clobber_ocr_rows 一直在守这一点）
        #     ② 有文本 + 未给 --force → 跳过
        #   缺陷在于①放行后又被②拦住：**只给 --redo-ocr（不给 --force）时完全无效**，
        #   而 help 明说该参数"允许对已 OCR 的图片型文献重新抽文本"
        #   （2026-10-08 用真实登记表复现：默认 0 篇、--redo-ocr 仍 0 篇、
        #   必须 --redo-ocr --force 才变 4 篇）。
        #   现在合并为一条判据：OCR 行仅凭 --redo-ocr 即放行，且不受 --force 影响。
        if row.get("解码状态") == "已OCR":
            if not args.redo_ocr:
                continue
        elif have and os.path.exists(have) and not args.force:
            continue
        # ★去重行不得被抽取。sync_corpus 判定它与另一行内容相同时，已把
        #   档位置为「去重-重复」、纳入判定置为「去重」、状态置为「跳过」——
        #   那是**"不要处理这一行"的决定**。
        #   历史缺陷（2026-10-08 用真实 PDF 复现）：本脚本只按"有没有文本"挑行，
        #   于是照抽去重行、把「跳过」覆盖成「已抽文本」，产出两份 sha1 完全相同的
        #   文本——**正好制造了它自己在报告里警告的"同一篇文献被读两遍"**，
        #   并使该行自相矛盾（档位说重复、状态说已抽）。
        #   这里只跳过并计数（不静默），从而兑现 docstring 里"不改判断字段"的承诺。
        if is_dedup_row(row):
            dup_skipped += 1
            continue
        todo.append(row)

    if args.limit:
        todo = todo[:args.limit]

    msg = f"待抽文本 {len(todo)} 篇（已抽的跳过）"
    if dup_skipped:
        msg += f"；另有 {dup_skipped} 篇已判为重复，按登记表的去重决定跳过"
    print(msg)
    if args.dry_run:
        for row in todo:
            print(f"  · {row['编号']}  {row['文件名']}")
        return 0

    results = {"ok": 0, "需OCR": 0, "打不开": 0}
    notes = []
    for row in todo:
        out = os.path.join(args.outdir,
                           f"{row['编号']}_{safe_filename(row.get('标题') or row['文件名'])}.txt")
        status, pages, chars, msg = extract_one(row["源路径"], out, extractor,
                                                with_printed_labels=args.printed_labels)
        results[status] = results.get(status, 0) + 1
        row["页数"] = str(pages) if pages else row.get("页数", "")
        row["字符数"] = str(chars) if chars else row.get("字符数", "")
        if status == "ok":
            row["文本路径"] = out
            row["解码状态"] = "已抽文本"
        else:
            row["文本路径"] = ""
            row["解码状态"] = status
        if msg:
            row["备注"] = (row.get("备注", "") + "；" + msg).strip("；")
            notes.append(f"{row['编号']} {row['文件名']}：{msg}")
        print(f"  [{status}] {row['编号']}  {row['文件名']}"
              + (f"  ({pages} 页 / {chars} 字)" if status == "ok" else f"  ← {msg}"))

    # 抽取文本层面的版本重复检查（文件字节不同但内容一致）
    dup_pairs = []
    if not args.no_dup_check:
        dup_pairs = find_text_duplicates(rows)
        for primary, dup in dup_pairs:
            note = (f"与 {primary['编号']}（{primary['文件名']}）文本层一致、"
                    f"文件字节不同：版本重复，建议只读 {primary['编号']}")
            if note not in dup.get("备注", ""):
                dup["备注"] = (dup.get("备注", "") + "；" + note).strip("；")
        if dup_pairs:
            print(f"\n⚠️  检出 {len(dup_pairs)} 对「文本一致、字节不同」的版本重复"
                  "（同一篇文献被读两遍的风险）：")
            for primary, dup in dup_pairs:
                print(f"   保留 {primary['编号']} ←→ 重复 {dup['编号']}  {dup['文件名'][:50]}")

    save_registry(args.registry, fields, rows)

    report = args.report or os.path.join(args.outdir, "_提取报告.md")
    body = [
        "# 批量抽文本报告", "",
        f"- 本次处理：{len(todo)} 篇",
        f"- 成功：{results.get('ok', 0)}",
        f"- 需 OCR：{results.get('需OCR', 0)}",
        f"- 打不开：{results.get('打不开', 0)}",
        f"- 文本层版本重复对：{len(dup_pairs)}", "",
    ]
    if dup_pairs:
        body += ["## 版本重复（文本一致、文件字节不同）— 需人工决定保留哪一份", "",
                 "| 建议保留 | 重复篇 | 文件 |", "|---|---|---|"]
        body += [f"| {p['编号']} | {d['编号']} | {d['文件名']} |" for p, d in dup_pairs]
        body += ["", "> 脚本只报告，不改档位/纳入判定/解码状态——那三项是人的判断。", ""]
    if notes:
        body += ["## 需要处理的篇目", ""] + [f"- {n}" for n in notes] + [""]
    with open(report, "w", encoding="utf-8") as fh:
        fh.write("\n".join(body))
    print(f"\n完成：成功 {results.get('ok', 0)}，需OCR {results.get('需OCR', 0)}，"
          f"打不开 {results.get('打不开', 0)}；报告 {report}")
    print("提示：需 OCR 的篇目不要跳过——空文本会被下游误读成「原文没写」。")
    return 2 if results.get("需OCR") else 0


if __name__ == "__main__":
    sys.exit(main())
