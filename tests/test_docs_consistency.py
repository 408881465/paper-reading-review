"""文档一致性回归网：把 2026-10-08 严格审计发现的问题固化为测试。

这些缺陷的共性是**文档自相矛盾**——脚本照常运行、既有测试全过，
但人会读到互相打架的说明。只能靠专门的文档检查拦住。

每条测试都对应一次真实事故，注释里写了事故是什么。
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

DOCS = [
    "SKILL.md",
    "README.md",
    "references/reading-codes.md",
    "references/review-workflow.md",
    "references/batch-workflow.md",
    "references/cited-literature.md",
]

TEMPLATES = [
    "assets/single-review-template.md",
    "assets/quick-review-template.md",
    "assets/multi-review-template.md",
    "assets/comparative-review-template.md",
    "assets/decode-card-template.md",
]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _sections(block: str):
    """取 `## 一、标题（…）〔…〕` 的 (节号, 节名)，节名剥掉括注。"""
    return re.findall(r"^## ([一二三四五六七八九十]+)、(.+?)(?:（|〔|\s*$)", block, re.M)


# ---------------------------------------------------------------- 形态数

def test_no_ambiguous_form_count():
    """同一规则不能既说「三形态」又说「四形态」。

    事故：description 写「三形态均含引用文献节」，第 4b 步写「四形态共用」，
    两个数字指的是同一条规则，读者无法判断到底几种形态共享它。
    统一写法是「各形态」——避开数数，就不需要维护两处。
    """
    for d in DOCS:
        t = _read(d)
        assert "三形态均含" not in t, f"{d}：用「三形态均含」，应写「各形态均含」"
        assert "四形态共用" not in t, f"{d}：用「四形态共用」，应写「各形态共用」"


# ---------------------------------------------------------------- 同义异名

def test_cited_literature_section_uses_canonical_name():
    """「值得关注的引用文献」是规范节名；「值得追的文献」是历史别名。

    事故：速览模板用了别名，而自检清单按规范名逐条核对——
    按规范名做的机械检查会漏掉该节，人工核对也会对不上。
    """
    bad = [d for d in DOCS + TEMPLATES if "值得追的文献" in _read(d)]
    assert not bad, f"以下文件仍用别名「值得追的文献」：{bad}"


def test_reference_list_sections_are_labelled():
    """「引用信息」与「参考文献」是两个不同概念，骨架里必须就地标出差别。

    事故：形态 A 的「引用信息」＝原文的文献表（原料），
    形态 B/C 的「参考文献」＝本次产出自己的文献表；两者都带 GB/T 7714，
    并列出现时读者会以为是一回事。
    """
    wf = _read("references/review-workflow.md")
    assert "原文的**文献表" in wf or "〔**原文的**文献表" in wf, \
        "形态 A 骨架的「引用信息」节未标注它是原文的文献表"
    assert "本综述自己的文献表" in wf and "本评述自己的文献表" in wf, \
        "形态 B/C 骨架的「参考文献」节未标注它是本次产出的文献表"


# ---------------------------------------------------------------- 骨架 vs 模板

def test_form_c_skeleton_matches_template():
    """形态 C 骨架与模板的节号曾错位一节。

    事故：骨架把「对比矩阵」当成"第二节之后的插入块"（不占节号，共 9 节），
    模板把它编为「二」（共 10 节）→ 按骨架写作的人与按模板检查的人对不上。
    """
    wf = _read("references/review-workflow.md")
    blk = wf[wf.index("### 4.1 文档骨架"):wf.index("### 4.2")]
    skel = _sections(blk)
    tpl = _sections(_read("assets/comparative-review-template.md"))
    assert [a for a, _ in skel] == [a for a, _ in tpl], \
        f"形态 C 骨架节号 {[a for a, _ in skel]} ≠ 模板 {[a for a, _ in tpl]}"
    assert [b.strip() for _, b in skel] == [b.strip() for _, b in tpl], \
        f"形态 C 节名不一致：\n  骨架 {[b for _, b in skel]}\n  模板 {[b for _, b in tpl]}"


def test_form_b_skeleton_matches_template():
    """形态 B 同理（当前一致，加测试防漂移）。"""
    wf = _read("references/review-workflow.md")
    blk = wf[wf.index("### 3.3 文档骨架"):wf.index("### 3.4")]
    skel = _sections(blk)
    tpl = _sections(_read("assets/multi-review-template.md"))
    assert [a for a, _ in skel] == [a for a, _ in tpl], \
        f"形态 B 骨架节号 {[a for a, _ in skel]} ≠ 模板 {[a for a, _ in tpl]}"


# ---------------------------------------------------------------- 文档与文件

def test_readme_tree_lists_every_shipped_file():
    """README 目录树是外人了解本技能的第一入口，漏文件＝宣称的能力少于实际。

    事故：README 落后过一整版——只说「三种产出形态」，目录树缺
    batch-workflow.md、三个形态 D 脚本、两个形态 D 模板、tests/。
    """
    m = re.search(r"```\n(paper-reading-review/.*?)```", _read("README.md"), re.S)
    assert m, "README 里找不到目录树代码块"
    block = m.group(1)
    missing = []
    for sub in ("references", "assets", "scripts"):
        for f in sorted((ROOT / sub).iterdir()):
            if f.is_file() and f.name not in block:
                missing.append(f"{sub}/{f.name}")
    assert not missing, f"README 目录树漏了：{missing}"
    assert "tests/" in block, "README 目录树未登记 tests/"


def test_skill_tree_lists_every_shipped_file():
    """SKILL.md 的目录树同样不得漏文件。"""
    m = re.search(r"```\n(paper-reading-review/.*?)```", _read("SKILL.md"), re.S)
    assert m, "SKILL.md 里找不到目录树代码块"
    block = m.group(1)
    missing = []
    for sub in ("references", "assets", "scripts"):
        for f in sorted((ROOT / sub).iterdir()):
            if f.is_file() and f.name not in block:
                missing.append(f"{sub}/{f.name}")
    assert not missing, f"SKILL.md 目录树漏了：{missing}"


# ---------------------------------------------------------------- 单一来源

def test_scanned_threshold_has_single_source():
    """「需OCR」阈值只能定义一处。

    事故：extract_pdf_text 与 batch_extract 各定义了一个 10；
    batch_extract 本已 import 前者，却没用它的常量——
    任一处被调，单篇抽取与批量抽取就会对同一页给出不同的「需OCR」判断。
    """
    be = _read("scripts/batch_extract.py")
    assert "_SCANNED_MIN_CHARS = " not in be, \
        "batch_extract 又本地定义了扫描件阈值，应复用 extract_pdf_text 的常量"
    assert "extractor.SCANNED_PAGE_MIN_CHARS" in be, \
        "batch_extract 未复用 extract_pdf_text.SCANNED_PAGE_MIN_CHARS"
    assert "SCANNED_PAGE_MIN_CHARS = 10" in _read("scripts/extract_pdf_text.py"), \
        "extract_pdf_text 的公开阈值常量不见了"
