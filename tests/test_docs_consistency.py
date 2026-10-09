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


# ═══════════════════════════════════════════════════════════════════════════════
# 登记表取值：**代码会写的，文档必须定义**
# 2026-10-09 机械审计查出（本会话占比最高的一类缺陷："文档写了要求、工具没实现"
# 的镜像——"工具写了取值、文档没定义"）。
# ═══════════════════════════════════════════════════════════════════════════════

# 脚本会写进登记表的取值。★新增取值时必须同步补文档与这张表。
STATE_VALUES = [
    # 档位
    "待分流", "T1核心", "T2重要", "T3背景", "专著章节", "政策文件", "去重-重复",
    # 解码状态
    "未处理", "已抽文本", "需OCR", "待其它提取器", "待核查", "跳过", "已OCR",
    "已解码", "已入RCOS", "已入综述", "打不开", "源文件缺失", "已分流",
]


def test_every_registry_state_the_scripts_write_is_documented():
    """★代码会写进登记表的取值，文档必须都定义——否则用户看到值却查不到含义。

    实测（2026-10-09 机械审计）：`待分流`（档位初值）、`未处理`（解码状态初值）、
    `源文件缺失`（异常态）三者**代码会写、文档各出现 0 次**。
    ★其中 `源文件缺失` 最要紧：它是异常态，用户看到它需要知道怎么办。
    （对照本文件里"文档引用的路径必须存在"那条——管的是引用，管不到取值。）
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    text = "".join(
        p.read_text(encoding="utf-8")
        for p in list((root / "references").glob("*.md"))
        + [root / "SKILL.md", root / "README.md"]
    )
    missing = [v for v in STATE_VALUES if v not in text]
    assert not missing, (
        f"这些取值脚本会写、文档却没定义：{missing}\n"
        f"→ 补进 references/batch-workflow.md 的状态取值表（§3.2）"
    )


def test_scripts_do_not_invent_undocumented_states():
    """★反向：脚本里新写的状态值，必须在 STATE_VALUES 里（即已被文档覆盖）。

    这条防止"悄悄加一个状态"——先在本测试登记、再补文档，两步都做完才算数。
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    src = "".join((root / "scripts" / n).read_text(encoding="utf-8")
                  for n in ("sync_corpus.py", "batch_extract.py"))
    known = set(STATE_VALUES)
    found = set(re.findall(r'"([\u4e00-\u9fff]{2,10})"', src))
    # 只看像状态值的：出现在 解码状态 / 档位 赋值右侧的
    assigned = set()
    for m in re.finditer(r'\["(?:解码状态|档位)"\]\s*=\s*"([^"]+)"', src):
        assigned.add(m.group(1))
    extra = sorted(assigned - known)
    assert not extra, (
        f"脚本会写这些取值，但 STATE_VALUES 未登记（也就未被文档覆盖）：{extra}\n"
        f"→ 补文档 + 补本测试的 STATE_VALUES"
    )


def test_criteria_do_not_leak_the_test_corpus():
    """★★ 判准文档不得包含测试语料的答案或可识别特征。

    2026-10-09 第三轮重测的事故：为消除第一轮的档位分歧，我在 batch-workflow §2.1
    里把那次实测的文章特征与结论都写了进去（连「结论是哪一档」都写明），
    而同一篇文章正是下一轮要测的对象——于是判准里写着答案，
    「两人一致」变成了自己考自己。

    这处污染是一位独立读者发现的（「我怀疑该表是按本文写就的，若如此，
    T2 是否等于已知答案？」）——写判准的人自己不会发现，故立此守护。

    规则：判准可以对「曾出现一处分歧」做抽象说明，但不得出现能指向具体测试文献的特征。
    扩充方式：每有一个新测试语料，就把其特征词加进下面禁表（并抽象化文档）。
    """
    root = pathlib.Path(__file__).resolve().parent.parent
    text = "".join(
        p.read_text(encoding="utf-8")
        for p in list((root / "references").glob("*.md")) + [root / "SKILL.md", root / "README.md"]
    )
    leaked = [k for k in ("意义—挑战—对策", "课标／师资／支持环境", "四条对策",
                          "规范社会培训机构", "该目标是否适用于初中和小学")
              if k in text]
    assert not leaked, (
        "判准文档里出现了测试语料的特征，等于泄露答案："
        + str(leaked)
        + " → 把它们抽象化（保留「为何加这条判据」的说明，去掉可识别特征与该篇的结论）"
    )
