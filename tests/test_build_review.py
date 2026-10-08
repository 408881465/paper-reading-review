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
HEADER = ["序号", "作者", "年份", "标题", "来源", "现有文献综述", "现有文献批评",
          "空白", "研究结果", "未来研究建议", "批评点/待探讨问题"]


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
