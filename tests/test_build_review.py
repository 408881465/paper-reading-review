"""build_review 的校验与聚类提示测试。"""

import csv
import subprocess
import sys
from pathlib import Path

from conftest import build_review as B
from conftest import load_module

_bm = load_module(B)

FIELD_ALIASES = _bm.FIELD_ALIASES
norm_header = _bm.norm_header
resolve_columns = _bm.resolve_columns

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
HEADER = ["序号", "作者", "年份", "标题", "来源", "现有文献综述", "作者对现有文献的批评",
          "空白", "研究结果", "作者对未来研究的建议", "批评点/待探讨问题"]


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(HEADER)
        writer.writerows(rows)
    return path


def row(n, author, year, spl, cpl, gap, rof):
    return [n, author, year, f"标题{n}", "期刊", spl, cpl, gap, rof, "建议", "批评"]


# ------------------------------------------------------------ 校验

def test_empty_table_is_a_blocking_problem(tmp_path, capsys):
    """只有表头时旧实现输出「✅ 检查通过」，等于放过「没填表就写综述」。"""
    path = write_csv(tmp_path / "empty.csv", [])
    _, _, problems, _ = B.check(str(path))
    assert problems, "空表必须报阻断问题"


def test_minimal_required_columns_pass(tmp_path):
    path = write_csv(tmp_path / "ok.csv", [
        row(1, "张三", 2020, "综述甲", "批评甲", "空白甲", "结果甲"),
        row(2, "李四", 2021, "综述乙", "批评乙", "空白乙", "结果乙"),
    ])
    _, _, problems, _ = B.check(str(path))
    assert problems == []


def test_single_author_domination_is_warned(tmp_path):
    """一树吊死：同一作者反复独占，必须预警。"""
    rows = [row(i, "张三", 2019 + i, f"综述{i}", "批评", "空白", "结果")
            for i in range(1, 5)]
    rows.append(row(5, "李四", 2024, "综述戊", "批评", "空白", "结果"))
    path = write_csv(tmp_path / "same.csv", rows)
    _, _, _, warnings = B.check(str(path))
    assert any("一树吊死" in w for w in warnings)


def test_identical_spl_column_is_warned(tmp_path):
    rows = [row(i, f"作者{i}", 2019 + i, "同一段综述文本", "批评", "空白", "结果")
            for i in range(1, 5)]
    path = write_csv(tmp_path / "copy.csv", rows)
    _, _, _, warnings = B.check(str(path))
    assert any("完全相同" in w for w in warnings)


def test_columns_accept_english_headers(tmp_path):
    path = tmp_path / "en.csv"
    path.write_text("no,author,year,spl,rof\n1,Zhang,2020,prev,res\n", encoding="utf-8")
    _, mapping, problems, _ = B.check(str(path))
    assert {"author", "year", "spl", "rof"} <= set(mapping)
    assert problems == []


# ------------------------------------------------------------ 聚类提示

def test_no_cross_phrase_fragments_in_candidates(tmp_path):
    """语料里反复出现「已有研究考察了课堂互动模式」时，
    旧实现会把「已有研究考察」「考察了课堂互动」等碎片排进前列。"""
    rows = [
        row(1, "甲", 2020, "已有研究考察了课堂互动模式", "多为小样本个案", "缺乏大规模证据", "互动频率与成绩正相关"),
        row(2, "乙", 2021, "已有研究考察了课堂互动模式", "缺乏对学科差异的考察", "缺乏学科比较", "理科互动显著少于文科"),
    ]
    path = write_csv(tmp_path / "edu.csv", rows)
    rows_loaded, _, _, _ = B.check(str(path))
    words = [w for w, _ in B.cluster_hint(rows_loaded)]
    assert "互动" in words
    for junk in ("已有研究考察", "考察了课堂互", "究考察了课堂", "研究关", "究关注"):
        assert junk not in words, f"碎片混进了候选主题：{junk}"


def test_top_topic_is_the_repeated_phrase(tmp_path):
    rows = [
        row(1, "甲", 2020, "已有研究多关注新闻价值的构成要素", "缺乏数字环境考察", "缺乏数字环境研究", "新闻价值判断标准位移"),
        row(2, "乙", 2021, "已有研究多关注新闻价值的构成要素", "机制未验证", "机制未验证", "算法影响新闻价值"),
        row(3, "丙", 2022, "已有文献已积累较多新闻价值研究", "未整合受众研究", "两类研究未整合", "受众参与影响新闻价值"),
    ]
    path = write_csv(tmp_path / "news.csv", rows)
    rows_loaded, _, _, _ = B.check(str(path))
    top = B.cluster_hint(rows_loaded)
    assert top and top[0][0] == "新闻价值"


def test_mark_dominated_removes_same_count_substrings():
    ranked = [("课堂互动模式", 3), ("课堂互动", 3), ("互动模式", 3), ("课堂", 3)]
    dominated = B.mark_dominated(ranked)
    assert "课堂互动" in dominated and "互动模式" in dominated


def test_long_phrase_does_not_suppress_displayable_topic():
    """超过展示上限的长句本就永远不输出，不该顺手压掉 6 字真主题。"""
    ranked = [("考察了课堂互动模式", 2), ("课堂互动模式", 2)]
    assert "课堂互动模式" not in B.mark_dominated(ranked)


def test_keep_whitelist_overrides_filters(tmp_path):
    """--stopwords 滤过头时，--keep 必须能把真主题救回来。"""
    rows = [
        row(1, "甲", 2020, "数字化转型推动教学变革", "批评", "空白", "结果"),
        row(2, "乙", 2021, "数字化转型影响教师角色", "批评", "空白", "结果"),
        row(3, "丙", 2022, "数字化转型重塑评价方式", "批评", "空白", "结果"),
    ]
    path = write_csv(tmp_path / "keep.csv", rows)
    rows_loaded, _, _, _ = B.check(str(path))
    filtered = B.cluster_hint(rows_loaded, extra_stopwords={"数字化转型"})
    assert not any(w == "数字化转型" for w, _ in filtered)
    restored = B.cluster_hint(rows_loaded, extra_stopwords={"数字化转型"},
                              keepwords={"数字化转型"})
    assert any(w == "数字化转型" for w, _ in restored)


def test_extra_stopwords_are_applied(tmp_path):
    rows = [
        row(1, "甲", 2020, "数字化转型推动教学变革", "批评", "空白", "结果"),
        row(2, "乙", 2021, "数字化转型影响教师角色", "批评", "空白", "结果"),
        row(3, "丙", 2022, "数字化转型重塑评价方式", "批评", "空白", "结果"),
    ]
    path = write_csv(tmp_path / "sw.csv", rows)
    rows_loaded, _, _, _ = B.check(str(path))
    assert any(w == "数字化转型" for w, _ in B.cluster_hint(rows_loaded))
    assert not any(w == "数字化转型"
                   for w, _ in B.cluster_hint(rows_loaded, extra_stopwords={"数字化转型"}))


# ------------------------------------------------------------ CLI

def run(*args, cwd=None):
    return subprocess.run([sys.executable, str(SCRIPTS / "build_review.py"), *args],
                          capture_output=True, text=True, cwd=cwd)


def test_init_writes_bom_for_excel(tmp_path):
    """Windows Excel 打开无 BOM 的 UTF-8 中文表头会乱码。"""
    out = tmp_path / "rcos.csv"
    assert run("init", str(out)).returncode == 0
    assert out.read_bytes()[:3] == b"\xef\xbb\xbf"


def test_cli_exit_code_1_on_empty_table(tmp_path):
    path = write_csv(tmp_path / "empty.csv", [])
    assert run("rcos", str(path)).returncode == 1


def test_cli_exit_code_0_on_valid_table(tmp_path):
    path = write_csv(tmp_path / "ok.csv", [
        row(1, "张三", 2020, "综述甲", "批评甲", "空白甲", "结果甲"),
        row(2, "李四", 2021, "综述乙", "批评乙", "空白乙", "结果乙"),
    ])
    assert run("rcos", str(path)).returncode == 0


def test_cli_missing_file_exits_1(tmp_path):
    assert run("rcos", str(tmp_path / "nope.csv")).returncode == 1


# --- 回归：2026-10-07「自己产的表头自己认不出」---------------
# init 生成的表头是「批评点/待探讨问题」，但 FIELD_ALIASES 里曾把它写成
# 带斜杠的原串；norm_header() 会剥离斜杠，于是该键匹配不上任何列——
# init 产物被自己的校验器判为「未识别的列」。别名必须写成归一化后的形态。

def test_every_alias_matches_its_normalized_form():
    """所有别名归一化后必须与自身的归一化键一致（否则是死别名）。"""
    for canon, aliases in FIELD_ALIASES.items():
        keys = {norm_header(a) for a in aliases}
        for alias in aliases:
            assert norm_header(alias) in keys, (
                f"{canon} 的别名「{alias}」归一化后不在别名表中，是死别名"
            )


def test_init_header_is_fully_recognized(tmp_path):
    """init 生成的表头必须被 resolve_columns 全部识别，不得有「未识别列」。"""
    out = tmp_path / "rcos.csv"
    assert run("init", str(out)).returncode == 0
    cols = next(csv.reader(out.open(encoding="utf-8-sig")))
    mapping, unknown = resolve_columns(cols)
    assert unknown == [], f"init 表头有列未识别：{unknown}"
    # 必需列必须都在
    for field in ("author", "year", "spl", "rof", "poc"):
        assert field in mapping, f"init 表头缺必需列映射：{field}"


# --- 回归：错位碎片与套话覆盖（2026-10-07）--------------------

def test_misaligned_fragments_are_suppressed():
    """滑窗在重复长短语上切出的「缺首字/缺尾字」碎片必须被抑制。

    语料「深度学习在教学中的应用」×4 会出现「深度学习在教」「度学习在教学」
    「学习在教学中」——三者互不为子串，mark_dominated 抓不住，
    靠 mark_misaligned 的「同频或更高频的完整版」判定。
    """
    rows = [{"spl": "深度学习在教学中的应用", "rof": "深度学习提升学习效果",
             "cpl": "缺乏对照", "gap": "对照缺失"} for _ in range(4)]
    words = [w for w, _ in B.cluster_hint(rows)]
    assert "学习在教学中" not in words
    assert "度学习在教学" not in words
    assert "学习在教" not in words
    # 真主题必须保留
    assert "深度学习" in words


def test_boilerplate_prefix_research_yi_is_cut():
    """「研究已积累」这类句式要切开，否则产出「值研究已积」类碎片。"""
    rows = [{"spl": f"新闻价值研究已积累{i}", "rof": f"影响新闻价值{i}",
             "cpl": f"缺判断标准{i}", "gap": f"机制未验证{i}"} for i in range(5)]
    words = [w for w, _ in B.cluster_hint(rows)]
    assert not any("积累" in w and w != "积累" for w in words)
    assert words[0] == "新闻价值", f"最高频主题词应为「新闻价值」，实得 {words[:3]}"


def test_no_misaligned_fragment_in_top_candidates():
    """候选列表里不得出现与更高频完整短语仅差一字、且同频的错位切片。"""
    rows = [{"spl": "混合式教学模式的实践", "rof": "混合式教学改善学习体验",
             "cpl": "样本单一", "gap": "样本不足"} for _ in range(3)]
    ranked = [(w, c) for w, c in B.cluster_hint(rows)]
    misaligned = B.mark_misaligned(ranked)
    assert not misaligned, f"候选中仍有错位碎片：{misaligned}"


# ---------------------------------------------------------------- --stopwords / --keep 的文件层
# （2026-10-08 用真实 RCOS 表实测后补：函数级行为已有测试，缺的是文件装载与 CLI 接线）

def test_load_stopwords_skips_comments_and_blanks(tmp_path):
    """`#` 注释与空行不得进词表——否则注释里的字会被当成停用词。"""
    f = tmp_path / "sw.txt"
    f.write_text("# 本课题语料的套话\n\n课程\n  人工智能教育  \n\n# 又一条注释\n建设\n",
                 encoding="utf-8")
    words = B.load_stopwords(str(f))
    assert words == {"课程", "人工智能教育", "建设"}
    assert not any(w.startswith("#") for w in words)


def test_expand_blocked_is_one_directional():
    """★展开是**单向**的：只把短语展开成它的子串，不向上覆盖更长的碎片。

    在 `expand_blocked` 层测——这是确定性机制，不受聚类启发式的取舍影响。
    实测（真实 RCOS 表）：停用「人工智能教育」（完整短语）能覆盖滑窗切出的
    碎片「人工智能教」；只停用「人工智能」（短形式）则覆盖不到它，
    因为「人工智能教」比它长、不是它的子串。**所以停用词要写完整短语。**
    """
    assert "人工智能" in B.expand_blocked({"人工智能"})
    assert "人工智能教" not in B.expand_blocked({"人工智能"}), "短形式本就不该向上覆盖"

    full = B.expand_blocked({"人工智能教育"})
    assert "人工智能教育" in full
    assert "人工智能教" in full, "完整短语应覆盖由它切出的碎片"
    assert "人工" in full and "智能" in full


def test_stopwords_substring_expansion_can_over_filter_and_keep_rescues():
    """★子串展开会连带滤掉真主题；`--keep` 是它的解药。

    停用「课程整合」会因展开而连带滤掉「课程」——这是 docstring 里承认的代价。
    """
    # 注意：词频需 ≥ min_count(2)，故同一短语要给到 3 行；
    # 且 DISPLAY_MAX=6，过长短语不会被展示。
    rows = [{"rof": f"人工智能教育课程建设与课程整合研究{i}", "spl": "", "cpl": "", "gap": ""}
            for i in range(3)]
    base = B.cluster_hint(rows)
    assert any(w == "课程" for w, _ in base), "前置条件：基线里应有「课程」"

    blocked = B.cluster_hint(rows, extra_stopwords={"课程整合"})
    assert not any(w == "课程" for w, _ in blocked), "子串展开应连带滤掉「课程」"

    rescued = B.cluster_hint(rows, extra_stopwords={"课程整合"}, keepwords={"课程"})
    assert any(w == "课程" for w, _ in rescued), "--keep 没能救回被连带滤掉的词"


def test_cli_stopwords_file_missing_exits_1(tmp_path):
    path = write_csv(tmp_path / "sw.csv", [
        row(1, "甲", 2020, "数字化转型推动教学变革", "批评", "空白", "结果"),
    ])
    res = run("rcos", str(path), "--stopwords", str(tmp_path / "没有这个文件.txt"))
    assert res.returncode == 1
    assert "不存在" in res.stderr


def test_cli_stopwords_and_keep_files_are_wired(tmp_path):
    rows = [
        row(1, "甲", 2020, "数字化转型推动教学变革", "批评", "空白", "结果"),
        row(2, "乙", 2021, "数字化转型影响教师角色", "批评", "空白", "结果"),
    ]
    path = write_csv(tmp_path / "sw.csv", rows)
    sw = tmp_path / "sw.txt"
    sw.write_text("数字化转型\n", encoding="utf-8")
    keep = tmp_path / "keep.txt"
    keep.write_text("数字化转型\n", encoding="utf-8")

    base = run("rcos", str(path))
    assert "数字化转型" in base.stdout

    filtered = run("rcos", str(path), "--stopwords", str(sw))
    assert "数字化转型" not in filtered.stdout

    restored = run("rcos", str(path), "--stopwords", str(sw), "--keep", str(keep))
    assert "数字化转型" in restored.stdout, "--keep 未接线到 CLI"


def test_skill_own_boilerplate_does_not_top_the_cluster_list():
    """★技能**自己规定**的填写句式，不得占据聚类榜首。

    实测（2026-10-08，课题真实 152 行 RCOS）：「批评」出现在 121/152 行（80%）、
    「空白」127/152（84%）、「作者未对」「本文评述推断」各 71/152（47%）——
    全部因为 batch-workflow §5 **要求**作者这样写（"作者未对任何前人文献提出批评"
    是合规写法、「（本文评述推断）」是必须标的归属）。
    技能教的写法污染技能自己的聚类，必须由技能自己停掉，不能推给用户 --stopwords。
    """
    # ★输入要够长：过短的文本（9 字 × 4 行）cluster_hint 返回**空表**，
    #   那样断言就退化成"空表里当然没有套话"，测了个寂寞（本测试第一版即栽在此）。
    rows = [{"rof": f"课程建设与跨学科协同机制的教学实践研究{i}",
             "spl": "作者未对前人文献提出批评",
             "cpl": "作者未明示空白（本文评述推断）", "gap": ""} for i in range(4)]
    words = [w for w, _ in B.cluster_hint(rows)]
    assert words, "前置条件：该输入应产出候选词，否则断言无意义"
    for boiler in ("批评", "空白", "作者未", "评述推断", "明示", "前人文献", "未对前人"):
        assert boiler not in words, f"技能自己的套话「{boiler}」仍进了候选主题词：{words}"
    # 只要求"有内容词存活"，不指定具体词形：聚类会合并成更长的短语。
    assert any(("教学" in w or "实践" in w or "课程" in w or "协同" in w) for w in words), \
        f"真主题词被挤掉了：{words}"


# ---------------------------------------------------------------- §5.3 列集一致性
# 2026-10-09 用形态 D 端到端跑批时查出：§5.3 规定的 RCOS 最小列集是 **20 列**，
# 而校验器只认 12 列、模板只有 11 列、init 里还硬编码了第三份 11 列——
# 其中就有 §5.3 点名"**不可省**"的 `理论依据`，写进去会被报「未识别的列（将忽略）」。
# ★2026-10-10：列集扩为 **21 列**——补入「作者要做什么」。原列集只有「作者实际做了什么」，
#   表中只剩交付、没有承诺，技能最强调的「承诺与交付必须成对读」在整合表里没法读。

SPEC_5_3 = ["编号", "作者", "年份", "标题", "来源", "现有文献综述", "作者对现有文献的批评",
            "现有文献研究空白", "理论依据（研究的必要性）", "作者要做什么",
            "研究结果（作者发现了什么）",
            "与现有文献观点一致的研究发现", "与现有文献观点相反的研究发现",
            "作者实际做了什么", "作者对未来研究的建议", "批评点", "待探讨的相关问题",
            "明显的遗漏点", "逻辑能否走通（能否自圆其说）", "主题分类", "一句话定位"]


def test_every_column_of_the_5_3_spec_is_recognised():
    """★§5.3 列集里的每一列都必须被 `FIELD_ALIASES` 认得。

    不认得的列会被报「未识别的列（将忽略）」——等于工具在劝用户删掉那一列。
    """
    known = set()
    for aliases in FIELD_ALIASES.values():
        known |= set(aliases)
    missing = [c for c in SPEC_5_3 if c not in known]
    assert not missing, f"§5.3 列集里这些列不被识别：{missing}"


def test_theory_column_is_recommended_not_required():
    """`理论依据` 按 §5.3 是"不是必需栏，但不可省"——故入建议栏而非必需栏。"""
    assert "rat" in _bm.RECOMMENDED
    assert "rat" not in _bm.REQUIRED


def test_wtd_column_is_recognised_and_recommended_not_required():
    """★「作者要做什么」必须被识别，且**不得**列为必需栏。

    2026-10-10 补入该列：原列集只有「作者实际做了什么」（交付），没有「作者要做什么」
    （承诺），于是整合表里**读不出对齐度**，形态 C 那一行也无处取数。

    ⚠️ 严重程度仍是"建议"而非"必需"：存量 RCOS（本课题已有 150+ 行）没有这一列，
    若判为必需，全部会报「缺少必需栏」——`RECOMMENDED` 只让覆盖率表显示「无此列」，
    既提示到位、又不把存量数据判成废表。
    """
    assert "wtd" in _bm.RECOMMENDED
    assert "wtd" not in _bm.REQUIRED
    assert _bm.label("wtd") == "作者要做什么", "缺中文显示名会让报告里出现 `wtd`"
    assert _bm.norm_header("作者要做什么") in {
        _bm.norm_header(a) for a in _bm.FIELD_ALIASES["wtd"]}
    mapping, unknown = _bm.resolve_columns(["作者要做什么", "作者实际做了什么"])
    assert unknown == []
    assert mapping["wtd"] == "作者要做什么" and mapping["wtdd"] == "作者实际做了什么", \
        "承诺与交付被映射到了同一栏——这两栏一旦合并，对齐度就无从核对"


def test_commit_promise_pair_is_both_in_the_spec():
    """★承诺与交付必须在列集里**成对存在**（只留一栏即等于没法核对对齐度）。"""
    assert "作者要做什么" in SPEC_5_3
    assert "作者实际做了什么" in SPEC_5_3


def test_init_and_shipped_template_agree_on_columns(tmp_path):
    """★三处表头必须是同一份：§5.3 文档、`assets/rcos-template.csv`、`init` 生成的。

    此前三处各写一份（20／11／11 列），合并与对账时会错列。
    """
    out = tmp_path / "tpl.csv"
    _bm.cmd_init(str(out))
    got = out.read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    assert got == SPEC_5_3, f"init 生成的表头与 §5.3 不符：{got}"


# ═══════════════════════════════════════════════════════════════════════════════
# 21 列 RCOS 的**唯一映射**：poc / rpp 撞名事件
# 2026-10-09 用一份 11 行 × 20 列的 RCOS 做形态 B 规模测试时查出（2026-10-10 扩为 21 列）。
# ═══════════════════════════════════════════════════════════════════════════════

def test_every_spec_column_maps_to_a_distinct_canonical():
    """★★ 关键：§5.3 的每一列必须**各自映射到唯一规范名**，不能两列撞一个。

    此前 `poc` 的别名集里同时含「批评点」与「待探讨的相关问题」，两列撞同一规范名，
    而 `resolve_columns` 的 `setdefault` 只保留第一个 →
    **「待探讨的相关问题」整列数据静默丢失**，且**既不在映射也不在 `unknown`**，
    报告还显示"未识别 0 列"——**完全看不出来**。

    ⚠️ 旧测试只断言"这一列出现在**某个**别名集里"，**查不出撞名**，故漏掉了它。
    """
    mapping, unknown = _bm.resolve_columns(SPEC_5_3)
    assert unknown == [], f"列集里有未识别的：{unknown}"
    assert len(mapping) == len(SPEC_5_3), \
        f"只映射了 {len(mapping)}/{len(SPEC_5_3)} 列——有两列撞了同一规范名"
    missing = [c for c in SPEC_5_3 if c not in mapping.values()]
    assert not missing, f"这些列静默落空：{missing}"


def test_poc_and_rpp_are_separate_canonicals():
    """「批评点」与「待探讨的相关问题」是两个密码，必须各占一个规范名。"""
    assert _bm.norm_header("批评点") in {_bm.norm_header(a) for a in _bm.FIELD_ALIASES["poc"]}
    assert _bm.norm_header("待探讨的相关问题") in {_bm.norm_header(a) for a in _bm.FIELD_ALIASES["rpp"]}
    mapping, _ = _bm.resolve_columns(["批评点", "待探讨的相关问题"])
    assert mapping["poc"] == "批评点" and mapping["rpp"] == "待探讨的相关问题"


def test_duplicate_canonical_is_reported_not_silently_dropped():
    """★撞规范名时，后一列必须进 `unknown` —— 不许静默丢弃。

    两个不同表头映射到同一规范名（如两份表的合并），若静默保留第一个，
    用户会以为全部列都读到了。
    """
    mapping, unknown = _bm.resolve_columns(["作者", "author"])
    assert len(mapping) == 1 and unknown == ["author"], \
        f"重复规范名的列未报出：mapping={mapping} unknown={unknown}"


def test_every_canonical_has_a_chinese_label():
    """★规范名一律要有中文显示名，否则报告里出现 `rat` 这种英文缩写。

    实测：覆盖率表曾把「理论依据」显示成 `rat`——我上一轮补了别名却漏了显示名。
    """
    missing = [c for c in _bm.FIELD_ALIASES if _bm.label(c) == c]
    assert not missing, f"这些规范名没有中文显示名，会以英文缩写出现在报告里：{missing}"


def test_skill_own_review_phrasing_fragments_are_stopped():
    """★技能**自己教用户写**的评述用语，其短碎片不得进入聚类候选。

    2026-10-09 用 11 行 RCOS 做 B 形态测试时，候选榜被
    `无数据 5`／`具体前人文献 4`／`作者明示 3`／`无实证 2`／`但未给样本 2` 占据——
    它们全是写「批评点／空白」栏时的评价用语，**不是文献的主题内容**。
    它们不是任何已列完整句的子串，故必须显式停用（技能教的写法污染技能自己的聚类，
    不能推给用户去 `--stopwords`）。
    """
    rows = [{"rof": "实践表明可提升素养", "spl": "", "cpl": "作者未对具体前人文献提出批评",
             "gap": "无数据；无实证；但未给样本；作者明示空白"}] * 3
    got = {w for w, _ in (B.cluster_hint(rows) or [])}
    for frag in ("无数据", "无实证", "具体前人文献", "作者明示", "未给样本"):
        assert frag not in got, f"评述用语碎片「{frag}」仍在聚类候选里：{sorted(got)}"


# ═══════════════════════════════════════════════════════════════════════════════
# 中英混排：引注人名与英文套话不得进主题候选
# 2026-10-09 用一份 8 行（英文 6 + 中文 2）的 RCOS 做形态 B 多语言测试时查出。
# ═══════════════════════════════════════════════════════════════════════════════

def test_citations_are_stripped_before_tokenizing():
    """★引注里的人名不是主题词。

    实测首跑候选榜出现 `fuchs 2`／`zorlu 2`——来自 RCOS 里写的
    「（Fuchs & Fuchs 1992 等）」「（Zorlu & Zorlu 2024）」。引注是**论据出处**，
    不是"文献在谈什么"；混进候选会把综述提纲带偏。
    """
    text = ("有：引用 1960s 以来 co-teaching 的历史（Trump 1966；Warwick 1971）"
            "与问题讨论（Fuchs & Fuchs 1992 等）。既有研究（Zorlu & Zorlu 2024）也如此。"
            "Wang et al. 2020 给出三要素。")
    cleaned = _bm.strip_citations(text)
    for name in ("Trump", "Warwick", "Fuchs", "Zorlu", "Wang"):
        assert name not in cleaned, f"人名 {name} 未被剥掉：{cleaned!r}"
    assert "co-teaching" in cleaned, "剥离过度：正文内容也被删了"


def test_english_boilerplate_is_stopped_but_topic_nouns_kept():
    """★英文**套话**要停，但可能是真主题的**名词**必须保留。

    实测：内置表此前只有 12 个英文功能词（the/and/for…），于是 `gap`／`focus`／`still`
    直接进了候选榜（gap 出现 4 次）。
    ★但同一次实测里 `teacher` 出现 **14 次、正是本主题最高频的真主题词**，
    故 teacher／student／collaboration／curriculum／stem 一律**不能停**。
    """
    boiler = {"gap", "gaps", "however", "still", "focus", "findings", "studies", "et"}
    for w in boiler:
        assert w in _bm.STOPWORDS, f"英文套话「{w}」未被停用"
    keep = {"teacher", "teachers", "student", "collaboration", "curriculum", "stem", "ai"}
    for w in keep:
        assert w not in _bm.STOPWORDS, f"可能是真主题的名词「{w}」被误停"


def test_mixed_language_clustering_surfaces_real_theme():
    """★中英混排时，候选榜应浮现**真主题**而不是噪声。

    用一小段中英混排语料：真主题是"教师协同"，噪声是引注人名与英文套话。
    """
    rows = [{
        "rof": "有真实数据：两位教师协同设计并授课（Kim & Kwon 2025）。Findings 表明……"
               "然而 however the study still has a gap。",
        "spl": "有：梳理既有研究（Fuchs & Fuchs 1992 等）。",
        "cpl": "作者未对具体前人研究提出批评。",
        "gap": "作者明示：leaving a gap in the integration。",
    }] * 3
    got = {w for w, _ in (_bm.cluster_hint(rows) or [])}
    for noise in ("fuchs", "kim", "kwon", "however", "still", "gap", "findings"):
        assert noise not in got, f"噪声「{noise}」仍在候选里：{sorted(got)}"


# ------------------------------------------------------------ 表头重复列

def _write_header_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    return path


def _dup_header_case(first, second):
    """两列指向同一密码：第 first 列、第 second 列各写一句话，便于分辨读到的是哪一列。"""
    header = ["作者", "年份", "现有文献综述", "研究结果（作者发现了什么）", first, second]
    values = ["甲", "2020", "综述内容", "研究发现", "【第一列空白】", "【第二列空白】"]
    return header, values


def test_exactly_duplicated_header_is_reported_not_silently_dropped():
    """★表头**逐字相同**的两列也必须报出来——此前只有"不同表头撞同一密码"才报。

    实测踩过：`resolve_columns([..., "现有文献研究空白", "现有文献研究空白"])`
    返回 `unknown == []`；报告写着"识别到的列: …"、**一条提示都没有**，
    而第二列的数据从头到尾没被读过。守卫写的是 `if raw not in mapping.values()`，
    恰好把**同名**这一种（最像手滑复制的一列）判成"已报过"而放行。
    """
    _, unknown = resolve_columns(["作者", "年份", "现有文献研究空白", "现有文献研究空白"])
    assert unknown == ["现有文献研究空白"], f"逐字重复的列名被静默吞掉：{unknown}"


def test_duplicate_header_reads_the_first_column(tmp_path):
    """★同名两列时必须读**第一列**——`csv.DictReader` 会把值覆盖成第二列。

    实测踩过：两列都叫「现有文献研究空白」，第一列写「【第一列空白】」、
    第二列写「【第二列空白】」，`load_rows` 读回的是**第二列**，第一列整列静默消失。
    `resolve_columns` 的既有口径是"保留第一个"，取数就必须跟着取第一个，
    否则映射与取值**各按一套口径**，报告与聚类都在拿另一列的数据算。
    """
    header, values = _dup_header_case("现有文献研究空白", "现有文献研究空白")
    path = _write_header_csv(tmp_path / "dup_same.csv", header, [values])
    rows, mapping, _ = B.load_rows(str(path))
    assert mapping["gap"] == "现有文献研究空白"
    assert rows[0]["gap"] == "【第一列空白】", \
        f"读到的不是第一列（列位口径与 resolve_columns 不一致）：{rows[0]['gap']!r}"


def test_alias_collision_also_reads_the_first_column(tmp_path):
    """★守卫（防改过头）：用**别名**撞名时，取数口径必须仍是第一列。

    `现有文献研究空白` + `空白` 这类撞名此前已能报出（进 `unknown`），
    且因 `DictReader` 的键取的是第一列的名字，取数**本来就是对的**。
    改列位取数时最容易顺手取"最后一次出现"，把这里反而改坏——故固定住。
    """
    header, values = _dup_header_case("现有文献研究空白", "空白")
    path = _write_header_csv(tmp_path / "dup_alias.csv", header, [values])
    rows, mapping, unknown = B.load_rows(str(path))
    assert mapping["gap"] == "现有文献研究空白"
    assert unknown == ["空白"], f"别名撞名必须报出后一列：{unknown}"
    assert rows[0]["gap"] == "【第一列空白】", f"读到的不是第一列：{rows[0]['gap']!r}"


def test_duplicate_column_is_surfaced_by_check(tmp_path):
    """★用户看到的是 `check` 的输出：重复列必须出现在提示里，不能"无提示 + 检查通过"。"""
    header, values = _dup_header_case("现有文献研究空白", "现有文献研究空白")
    path = _write_header_csv(tmp_path / "dup_same.csv", header, [values])
    _, _, problems, warnings = B.check(str(path))
    assert any("现有文献研究空白" in w for w in warnings + problems), \
        f"重复位列没有出现在任何提示里：warnings={warnings} problems={problems}"


# ------------------------------------------------------------ 覆盖率表的「承诺 / 交付」对

def test_wtdd_column_is_shown_in_the_coverage_table(tmp_path, capsys):
    """★「作者实际做了什么」必须印在覆盖率表里——它是「作者要做什么」的**对端**。

    实测踩过：2026-10-10 列集由 20 列扩为 21 列、补入「作者要做什么」，
    `RECOMMENDED` 同步加了 `wtd`，**却漏了 `wtdd`**——于是覆盖率表里
    「承诺」有、「交付」没有：技能最强调的「承诺 vs 交付」对齐度，
    在工具输出里只剩一半；用户整列漏填「作者实际做了什么」也得不到任何提示
    （它既不进覆盖率表，又不在 `REQUIRED` 里）。
    """
    path = _write_header_csv(
        tmp_path / "full21.csv", SPEC_5_3,
        [["1", "甲", "2020", "题", "刊"] + ["内容"] * (len(SPEC_5_3) - 5)])
    B.check(str(path))
    out = capsys.readouterr().out
    assert "密码栏覆盖率:" in out
    # ★断言**只看覆盖率表那一段**：`check` 在此之前会印一行「识别到的列: …」，
    #   它把全部已映射的表头都列过一遍——拿整份 out 去断言，未修代码也会
    #   **因错误理由通过**（本测试首版就这么假绿过一次）。
    table = out.split("密码栏覆盖率:", 1)[1]
    assert "作者要做什么" in table, "覆盖率表漏印了「作者要做什么」（承诺端）"
    assert "作者实际做了什么" in table, "覆盖率表漏印了「作者实际做了什么」（交付端）"


def test_wtdd_is_recommended_not_required_and_keeps_spec_order():
    """★登记口径：`wtdd` 入**建议栏**（存量 RCOS 不该因缺它而报错），
    且按 §5.3 列序排在 `wtd` 之后——两栏在覆盖率表里读起来才是相邻的一对。
    """
    assert "wtdd" in _bm.RECOMMENDED, "「作者实际做了什么」不在建议栏，覆盖率表永远不会显示它"
    assert "wtdd" not in _bm.REQUIRED, "它是建议栏——判成必需会让存量 RCOS 全部报「缺少必需栏」"
    rec = list(_bm.RECOMMENDED)
    assert rec.index("wtdd") > rec.index("wtd"), f"§5.3 列序被破坏：{rec}"
    assert _bm.label("wtdd") == "作者实际做了什么"


# ------------------------------------------------------------ 缺栏文案

def _missing_required_msg(tmp_path, name, r):
    path = write_csv(tmp_path / name, [r])
    _, _, problems, _ = B.check(str(path))
    return next((p for p in problems if "缺少必需栏" in p), None)


def test_missing_required_message_names_only_the_fields_actually_missing(tmp_path):
    """★缺栏文案只能列出**真的缺**的那几栏，不能固定拼 `REQUIRED` 四项。

    实测踩过：只缺「年份」时（覆盖率表里 年份 0/1、其余必需栏 100%），
    文案仍写「缺少必需栏（作者、年份、现有文献综述、研究结果（作者发现了什么））」，
    并附上「缺研究结果无法做主题聚类，缺现有文献综述无法构建综述骨架」——
    用户会去找**根本不缺**的那三栏，且以为综述骨架已经没法做了。
    """
    msg = _missing_required_msg(tmp_path, "only_year.csv",
                                row(1, "甲", "", "综述", "批评", "空白", "发现"))
    assert msg, "只缺「年份」时没有报缺栏"
    assert "年份" in msg
    for absent in ("作者", "现有文献综述", "研究结果"):
        assert absent not in msg, f"「{absent}」并未缺失，却出现在缺栏文案里：{msg}"


def test_missing_required_message_keeps_the_consequence_for_real_gaps(tmp_path):
    """★后果说明要与**真实缺的那几栏**对应；未缺的栏不得带出它的后果。"""
    # 缺「现有文献综述」与「研究结果」两栏
    msg = _missing_required_msg(tmp_path, "no_spl_rof.csv",
                                row(1, "甲", "2020", "", "批评", "空白", ""))
    assert msg
    assert "现有文献综述" in msg and "研究结果" in msg
    assert "无法做主题聚类" in msg and "无法构建综述骨架" in msg, \
        f"真缺了这两栏，后果说明反而没了：{msg}"
    assert "年份" not in msg, f"「年份」并未缺失，却出现在文案里：{msg}"


def test_consequence_hint_is_conditional_on_which_field_is_missing(tmp_path):
    """★两条后果说明各自独立触发：只缺「研究结果」时不该提「构建综述骨架」。"""
    msg = _missing_required_msg(tmp_path, "no_rof.csv",
                                row(1, "甲", "2020", "综述", "批评", "空白", ""))
    assert msg
    assert "无法做主题聚类" in msg, f"缺「研究结果」却没提主题聚类：{msg}"
    assert "无法构建综述骨架" not in msg, \
        f"「现有文献综述」并未缺失，却给了构建骨架的后果说明：{msg}"
