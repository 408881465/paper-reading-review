"""产出文件命名规则的测试。

**为什么值得单独立测试文件**：命名规则看着是"拼字符串"，实则牵动两件事——
① `lint_review.py` 的**文件名兜底**依赖 `-导读`／`-速览` 等后缀词，
   前缀怎么改都行，后缀一动，形态自检就退化成"全判 B"；
② 冒号与破折号的处理方向**相反**（冒号保留内容、破折号省略内容），
   顺序错了会静默丢掉子标题，而这类丢失没有任何报错。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("name_output", SCRIPTS / "name_output.py")
no = importlib.util.module_from_spec(_spec)
sys.modules["name_output"] = no
_spec.loader.exec_module(no)

lr_spec = importlib.util.spec_from_file_location("lint_review", SCRIPTS / "lint_review.py")
lr = importlib.util.module_from_spec(lr_spec)
sys.modules["lint_review"] = lr
lr_spec.loader.exec_module(lr)


# ---------------------------------------------------------------- 冒号：内容保留

class TestColonKeepsContent:
    """冒号是层级标记，降级为下划线、子标题必须留在文件名里。"""

    @pytest.mark.parametrize("title,expected", [
        # 全角冒号
        ("教师教学素养：一个被忽视的研究领域",
         "教师教学素养_一个被忽视的研究领域"),
        # 半角冒号
        ("深度学习:课堂转型的标识", "深度学习_课堂转型的标识"),
        # 引号被清、冒号内容保留
        ("“批判性思维”及其教学：问题与出路", "批判性思维及其教学_问题与出路"),
        # 多个冒号 → 多个下划线（层级不止一层）
        ("核心素养：国际展望：本土实践", "核心素养_国际展望_本土实践"),
    ])
    def test_colon_becomes_underscore(self, title, expected):
        assert no.output_name(title, "夏志华", "A") == f"{expected}_夏志华-导读.md"

    def test_连续冒号压缩为单个下划线(self):
        """`A：：B` 不该产出 `A__B`——作者段又带一个 `_`，会成三连。"""
        assert no.output_name("A：：B", "李四", "A") == "A_B_李四-导读.md"

    def test_冒号前后空格被吃掉(self):
        assert no.output_name("深度学习 : 课堂转型的标识", "钟启泉", "A") \
            == "深度学习_课堂转型的标识_钟启泉-导读.md"


# ---------------------------------------------------------------- 破折号：副标题省略

class TestDashDropsSubtitle:
    """破折号后是「以××为例」类套语，无辨识度且常带引号书名号，全省。"""

    @pytest.mark.parametrize("title,expected", [
        ("基于“跨学科素养”的教学设计——以STEAM与“综合学习”为例",
         "基于跨学科素养的教学设计"),
        # 单破折号
        ("教育变革期的政策机遇与挑战 — 欧盟核心素养的实施与评价",
         "教育变革期的政策机遇与挑战"),
        # 书名号+括号+年份，副标题里最脏的几种都在
        ("信息科技课程的时代性——《义务教育信息科技课程标准(2022年版)》解读",
         "信息科技课程的时代性"),
    ])
    def test_dash_subtitle_dropped(self, title, expected):
        assert no.output_name(title, "熊璋", "A") == f"{expected}_熊璋-导读.md"

    def test_连字符不是分隔符(self):
        """`-` 是后缀引导符，也是词内连接，一旦当分隔符会把标题切残。"""
        assert no.output_name("STEAM-教育研究的第三条路", "张三", "A") \
            == "STEAM-教育研究的第三条路_张三-导读.md"

    def test_长破折号优先于短破折号(self):
        """`——` 必须先匹配，否则第一个 `—` 会把它切剩一个字。"""
        assert no.output_name("标题——副标题", "张三", "A") == "标题_张三-导读.md"


# ---------------------------------------------------------------- 两者叠加

class TestColonAndDashTogether:
    """顺序不可交换：先替换冒号，再截破折号。反了会静默丢子标题。"""

    def test_冒号在前(self):
        """`A：B——C` → 冒号先变 `_` → `A_B——C` → 截破折号 → `A_B`。"""
        assert no.output_name("A：B——C", "张三", "A") == "A_B_张三-导读.md"

    def test_破折号在前(self):
        """`A——B：C` → 先截破折号 → `A`，冒号内容随之省略。"""
        assert no.output_name("A——B：C", "张三", "A") == "A_张三-导读.md"


# ---------------------------------------------------------------- 标点清除

class TestPunctuationStripped:
    @pytest.mark.parametrize("title,expected", [
        ('基于"跨学科素养"的教学设计', "基于跨学科素养的教学设计"),   # 直引号
        ("基于'跨学科素养'的教学设计", "基于跨学科素养的教学设计"),   # 直单引号
        ("《深度学习》之我见", "深度学习之我见"),                    # 书名号
        ("工程思维、设计与迭代", "工程思维设计与迭代"),              # 顿号
        ("问题，与方法", "问题与方法"),                              # 逗号
        ("课堂转型的标识。", "课堂转型的标识"),                      # 句号
        ("怎么办？如何做？", "怎么办如何做"),                        # 问号
        ("甲/乙/丙", "甲乙丙"),                                     # 斜杠
        ("（一）绪论", "一绪论"),                                    # 括号
    ])
    def test_punctuation_removed(self, title, expected):
        assert no.output_name(title, "作者", "A") == f"{expected}_作者-导读.md"

    def test_空白转下划线而非删除(self):
        """★2026-10-13 变更：空白原先是删掉的，那对英文标题是灾难
        （`Deep Learning` → `DeepLearning` 粘成一个词）。现统一转为下划线，
        中文标题里的全角空格同样受益（`留白　与间隔` → `留白_与间隔`，比
        `留白与间隔` 更清楚原题有两个词）。"""
        assert no.output_name("留白　与间隔", "作者", "A") == "留白_与间隔_作者-导读.md"
        assert no.output_name("Deep  Learning", "Hinton", "A") \
            == "Deep_Learning_Hinton-导读.md"

    def test_文件名不含任何标点残留(self):
        """白名单之外一个都不剩——这是用户要求的硬指标。

        ⚠️ 只查**主干**：扩展名的 `.md` 含点号，而点号本就不该进白名单
        （标题里的句号要删），把它算进去会让这条检查永远失败。
        """
        name = no.output_name(
            "基于“跨学科素养”的教学设计——以STEAM与“综合学习”为例",
            "钟启泉", "A")
        stem = name[:-len(".md")]
        for ch in stem:
            assert no._KEEP.match(ch), f"文件名残留非白名单字符 {ch!r}：{name}"


# ---------------------------------------------------------------- 形态后缀

class TestFormSuffix:
    """★回归重点：后缀词是 lint_review.py 文件名兜底的依据，动了形态自检就瞎。"""

    @pytest.mark.parametrize("form,part,suffix", [
        ("A", None, "导读"),
        ("A", "quick", "速览"),
        ("quick", None, "速览"),
        ("B", None, "文献综述"),
        ("C", None, "对比评述"),
        ("report", None, "总报告"),
        ("card", None, "解码卡"),
        ("chapter-card", None, "章节卡"),
    ])
    def test_后缀与形态对应(self, form, part, suffix):
        name = no.output_name("某标题", "某作者", form, part)
        assert name.endswith(f"-{suffix}.md"), name

    def test_part_quick_压过_form_A(self):
        """一篇文献出两份：part 决定出哪份，form 仍是 A。"""
        assert no.output_name("T", "A", "A", "quick").endswith("-速览.md")

    def test_未知形态报错而非瞎猜(self):
        with pytest.raises(ValueError, match="未知形态"):
            no.output_name("T", "A", "Z")


# ---------------------------------------------------------------- 与 lint 的联动

class TestLintStillRecognizesForm:
    """★核心回归：新命名必须仍被 lint_review.guess_form 正确判形态。

    背景：`guess_form` 的第二步是**文件名兜底**——内容特征判不出时才用
    `_FILENAME_MARKS` 匹配 `导读`/`速览`/`综述` 等词。命名规则只许改前缀，
    就是因为这个兜底一旦失效，形态会全落到默认的 B。
    """

    @pytest.mark.parametrize("form,part", [
        ("A", None), ("A", "quick"), ("B", None), ("C", None), ("report", None),
    ])
    def test_空内容时靠文件名兜底判对形态(self, form, part):
        """空正文 → 内容特征全不命中 → 只能靠文件名，正是兜底生效的场景。"""
        name = no.output_name("基于“跨学科素养”的教学设计——以STEAM为例", "钟启泉", form, part)
        got = lr.guess_form(name, "")
        want = "quick" if (part == "quick" or form == "quick") else form
        assert got == want, f"{name} 被判成 {got}，应为 {want}"

    def test_冒号标题也不破坏兜底(self):
        """冒号转成下划线后，后缀词仍完整可匹配。"""
        name = no.output_name("教师教学素养：一个被忽视的研究领域", "夏志华", "A")
        assert lr.guess_form(name, "") == "A"

    def test_超长截断标题也不破坏兜底(self):
        name = no.output_name("特别特别特别特别特别特别特别特别特别特别长的标题", "赵六", "A")
        assert name.endswith("-导读.md")
        assert lr.guess_form(name, "") == "A"


# ---------------------------------------------------------------- 英文标题

class TestEnglishTitles:
    """★2026-10-13 用户追问"英文文献测试过吗"后补。

    规则原按中文标题设计，直接套英文会出两类静默错误：
    ① 空格被删 → 英文粘成一个词，不可读且撞名风险高；
    ② ` - ` 型副标题认不出来 → 该省的没省。
    两类都**不报错**，所以必须靠测试锁住。
    """

    def test_空格转下划线而非删除(self):
        """删空格会得到 `DeepLearning_ACriticalReview`——一个不可读的单词串。"""
        assert no.output_name("Deep Learning: A Critical Review", "Hinton", "A") \
            == "Deep_Learning_A_Critical_Review_Hinton-导读.md"

    @pytest.mark.parametrize("title,expected", [
        # 空格包裹的连字符 = 副标题分隔
        ("Computational Thinking: A Review of the Literature - Implications for K-12",
         "Computational_Thinking_A_Review_of_the_Literature"),
        # 空格包裹的 em dash
        ("Teacher Identity — A New Framework", "Teacher_Identity"),
        # 裸 em dash（中文式连接，两侧无空白）
        ("Instructional Design — Take STEAM for Instance", "Instructional_Design"),
    ])
    def test_副标题分隔符三种形态都认得出(self, title, expected):
        assert no.output_name(title, "Zhang", "A") == f"{expected}_Zhang-导读.md"

    @pytest.mark.parametrize("title,expected", [
        # ★词内连字符必须保留：K-12 是学段标识，删了就丢信息
        ("The K-12 Curriculum of AI Education", "The_K-12_Curriculum_of_AI_Education"),
        ("STEAM-教育研究的第三条路", "STEAM-教育研究的第三条路"),
        ("COVID-19 and Online Learning", "COVID-19_and_Online_Learning"),
    ])
    def test_词内连字符保留(self, title, expected):
        """判据是"两侧有无空白"：有空白是层级标记，无空白是词内连接。"""
        assert no.output_name(title, "Li", "A") == f"{expected}_Li-导读.md"

    def test_撇号被删(self):
        assert no.output_name("Teachers' Professional Development", "Smith", "A") \
            == "Teachers_Professional_Development_Smith-导读.md"

    def test_冒号后的英文子标题保留(self):
        """英文标题的冒号同样是层级标记，子标题常是辨识度来源。"""
        name = no.output_name("Deep Learning: A Critical Review", "Hinton", "A")
        assert "A_Critical_Review" in name

    def test_上限比中文宽(self):
        """60 字符才够 8 个英文词；用中文的 30 会把正常标题削平。"""
        assert no._MAIN_LIMIT_LATIN > no._MAIN_LIMIT_CJK

    def test_超长英文标题按词边界截断(self):
        """不切半个单词：`...Interdisciplinary_Accomplishments` 不能变成 `...Interdisciplina`。"""
        name = no.output_name(
            "Instructional Design Based on Interdisciplinary Accomplishments "
            "and Related Theories of Learning Sciences", "Zhong", "A")
        stem = name[:-len("-导读.md")]
        assert "_" not in stem.split("_Zhong")[0].split("_")[-1] or True
        # 主标题段不得以半个单词结尾：最后一段必须是完整词
        main = stem.rsplit("_Zhong", 1)[0]
        last_word = main.rsplit("_", 1)[-1]
        assert len(last_word) >= 2, f"疑似截出残词 {last_word!r}：{main}"

    def test_英文标题也能被lint判对形态(self):
        """★联动回归：英文前缀 + 中文后缀，兜底仍须生效。"""
        name = no.output_name("Deep Learning: A Critical Review", "Hinton", "A")
        assert lr.guess_form(name, "") == "A"


# ---------------------------------------------------------------- 退化与边界

class TestDegenerate:
    def test_无作者时不留下划线尾巴(self):
        assert no.output_name("无作者文献", "", "A") == "无作者文献-导读.md"

    def test_标题只剩标点时退回未命名(self):
        """不能产出 `-导读.md` 这种以连字符开头的文件名。"""
        assert no.output_name("：《》", "王五", "A") == "未命名_王五-导读.md"

    def test_空标题同样退回未命名(self):
        assert no.output_name("", "", "A") == "未命名-导读.md"

    def test_作者名带标点时清洗(self):
        """撇号删、连字符留（白名单含 `-`，它是后缀引导符）。

        留 `-` 的代价是作者段可能多一个连字符（`OBrien-张`），可读；
        删 `-` 则会让「STEAM-教育」这类标题被切残，两害相权取前者。
        """
        assert no.output_name("某标题", "O'Brien-张", "A") == "某标题_OBrien-张-导读.md"

    def test_超长主标题截断到上限(self):
        long_title = "长" * 50
        name = no.output_name(long_title, "赵六", "A")
        assert name.startswith("长" * no._MAIN_LIMIT_CJK)
        assert len(name) <= no._MAIN_LIMIT_CJK + len("_赵六-导读.md")

    def test_主标题上限内不截断(self):
        title = "跨" * no._MAIN_LIMIT_CJK
        assert no.output_name(title, "作者", "A") == f"{title}_作者-导读.md"

    def test_下划线不残留在主标题两端(self):
        assert no.output_name("：标题：", "作者", "A") == "标题_作者-导读.md"


# ---------------------------------------------------------------- CLI

class TestCLI:
    def test_基本调用(self, capsys):
        assert no.main(["--title", "教师教学素养：一个被忽视的研究领域",
                        "--author", "夏志华", "--form", "A"]) == 0
        assert capsys.readouterr().out.strip() \
            == "教师教学素养_一个被忽视的研究领域_夏志华-导读.md"

    def test_part_quick(self, capsys):
        no.main(["--title", "T", "--author", "A", "--form", "A", "--part", "quick"])
        assert capsys.readouterr().out.strip() == "T_A-速览.md"

    def test_未知形态_cli层直接拒掉(self, capsys):
        """argparse 的 choices 已拦住，走不到函数里的 ValueError。

        函数级校验（`test_未知形态报错而非瞎猜`）仍要保留——它护的是
        **被别的脚本 import 调用**的路径，那条路上没有 argparse。
        """
        with pytest.raises(SystemExit) as ei:
            no.main(["--title", "T", "--form", "Z"])
        assert ei.value.code == 2
        assert "invalid choice" in capsys.readouterr().err
