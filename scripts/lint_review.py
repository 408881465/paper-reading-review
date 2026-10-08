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
    "C": (800, 15000), "card": (200, 2500),
    # 章节级解码卡面对的是 100–300 页的专著/学位论文，普通 T2 卡的上限套不过来。
    # 实测：不给它单独的规格，队友就会「把 T2 当小型详版写」或「把 200 页书压成一张卡」，
    # 两种做法都让档位分层失效（独立校验 2026-10-07 报出 15 张卡超限）。
    "chapter-card": (400, 5000),
    "report": (0, 10 ** 9),
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
_LOCATOR = re.compile(r"(?:p\.\s*\d+|第\s*\d+\s*[页节]|（\s*第\s*\d+\s*页\s*）)")
_MERGED = re.compile(r"(?:研究|分析|探讨)了?.{2,40}(?:并|且|同时)发现")
_PRAISE = re.compile(r"本文评述")

# ---- hygiene：脚本产物污染（2026-10-08 实战，两类都会静默混进正文） ----
# ① HTML 实体：`&#x7684;`（＝「的」）。某些库/工具把非 ASCII 转义后未回转。
#    正文里出现即为污染，无正当用途。
_HTML_ENTITY = re.compile(r"&#x?[0-9A-Fa-f]{2,6};")
# ② 反斜杠引用字面泄漏：`re.sub` 的替换体写成 r"\1" + text（而非函数）时，
#    `\1` 会被当字面量输出。特征：`\数字` 紧贴汉字，或独占行首/行尾。
#    正则讨论通常写在行内代码里，故检查前先剥掉代码块与行内代码，避免假警报
#    （假警报的代价见 DOC_FORM_SKIP 处说明：人一旦忽略 lint 输出，它就等于不存在）。
#    ★前后一律用显式 ASCII 判据，**不能用 \w 或 \b**——Python 的 \w 包含中文，
#    `\1协同` 这种「紧贴汉字」的泄漏恰恰是最典型的形态，用 \w 会整类漏掉。
_BACKREF_LEAK = re.compile(r"(?<![A-Za-z0-9_\\])\\[1-9](?![A-Za-z0-9_])")
_FENCE_BLOCK = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`[^`\n]*`")


def strip_code(text: str) -> str:
    """剥掉围栏代码块与行内代码，只留正文——用于「污染」类检查。"""
    return _INLINE_CODE.sub("", _FENCE_BLOCK.sub("", text))



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


def guess_form(path: str, text: str) -> str:
    name = path.rsplit("/", 1)[-1]
    # 技能自带的说明与模板按「规范文档」处理，不套 review 规则
    if {"references", "assets"} & set(re.split(r"[\\/]", path)):
        return "doc"
    if "速览" in name:
        return "quick"
    # >60 页的专著/学位论文按章解码，规格另计。要求"章节"与"卡/解码"同时出现——
    # 只看"章节"会把「研究现状章节素材.md」这类普通文档误判成章节级卡（实测踩过）。
    if "章节" in name and ("卡" in name or "解码" in name):
        return "chapter-card"
    if "解码卡" in name:
        return "card"
    if "对比" in name or "评述" in name:
        return "C"
    if "总报告" in name:
        return "report"
    if "综述" in name:
        return "B"
    if "导读" in name:
        return "A"
    # 结构兜底：有「作者提出的主要问题」且无多篇文献表 → 视作详版
    if "作者提出的主要问题" in text:
        return "A"
    return "B"


def _inside_paren(text: str, pos: int) -> bool:
    """判断 pos 处的缩写是否处在括注里（`研究结果（ROF）` 的合规用法）。"""
    i = pos - 1
    while i >= 0 and text[i] in " \t":
        i -= 1
    return i >= 0 and text[i] in "（("


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
    if "abbr" not in off:
        in_fence = False
        for line in text.splitlines():
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
    if "banned" not in off:
        for word, why in BANNED:
            if word in text:
                errors.append(f"禁用表述「{word}」：{why}")

    # 4) 空白必配理论依据（逐节配对，不只查全文）
    if "gap-rat" not in off:
        for i, (title, body) in enumerate(sections):
            if "空白" in title or "研究空白" in title:
                nxt = sections[i + 1] if i + 1 < len(sections) else ("", "")
                if "理论依据" in body or "理论依据" in nxt[0] + nxt[1]:
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
            missing = [n for n in CODE_NAMES[:10] if n not in text]
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
    if "limits" not in off:
        if "局限" in text and not _PRAISE.search(text):
            warnings.append("出现「局限」但全文无「本文评述」标记："
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
