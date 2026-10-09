# -*- coding: utf-8 -*-
"""compare_cards 的口径一致性比对测试。

四条断言都对应**我自己临时脚本连栽三次**的真实 bug（2026-10-09 协作层实测）。
"""

import pytest

import compare_cards as cc


def test_tier_ignores_menu_string():
    """★档位判定必须剥掉「T1核心／T2重要／T3背景」这种**菜单串**。

    实测踩过：朴素子串匹配在菜单上命中第一个 T 编号，把两份卡都读成 T1——
    而真实结论是 T3 与 T2。
    """
    card = ("| 档位 | **T3 背景** / 纳入（背景） |\n"
            "判准：T1核心／T2重要／T3背景 三选一，本文判 T3。\n")
    tier, _ = cc.find_tier(card)
    assert tier == "T3背景", f"菜单串干扰了档位判定：{tier}"

    # ★必须让**第一个候选**就含菜单串，否则测不到"剥菜单"这一步：
    #   若档位表格行同时给出结论，无论剥不剥菜单都会命中它。
    card2 = ("**档位：T2 重要**\n"
             "判准：T1核心／T2重要／T3背景 三选一。\n")
    assert cc.find_tier(card2)[0] == "T2重要"

    # 更毒的一种：档位那一行**就是**菜单串本身，真结论在其后
    card3 = "| 档位 | T1核心／T2重要／T3背景 |\n本文判 **T3 背景**。\n"
    assert cc.find_tier(card3)[0] == "T3背景", "第一候选是菜单串时未剥掉"


def test_items_survive_multiline_sections():
    """★条目切分必须保留换行（`re.M` 的行锚点靠它）。

    实测踩过：早先在 `field_segment` 里就把空白压平，段变成一整行，
    5 条批评点只切出 1 条。
    """
    card = ("### 批评点\n\n"
            "1. 第一条批评，给了原文依据。\n"
            "2. 第二条批评，给了原文依据。\n"
            "3. 第三条批评，给了原文依据。\n")
    got = cc.items(card, "批评点")
    assert len(got) == 3, f"只切出 {len(got)} 条：{got}"


def test_combined_section_is_not_taken_as_the_code_section():
    """★标题里带密码名、但不是**专节**的合并小节不能用。

    实测踩过：「## 3. 档位判据与空白判定（本卡必答项）」含「空白」，
    被误当空白专节，于是把**档位那段**读成了空白的答复 → 该栏误判成"有"。
    """
    card = (
        "| 空白（GAP） | **作者未提出研究意义上的空白** |\n"
        "## 3. 档位判据与空白判定（本卡必答项）\n"
        "① 档位：T2 重要。判据……\n"
    )
    assert cc.judge(card, "空白")[0] == "未提出", "合并小节把空白栏的判定带偏了"


def test_only_designated_codes_can_be_judged_absent():
    """★只有那 5 栏可以判「未提出」；其余栏只要写了内容就是「有」。

    实测踩过：「研究结果」被两份卡都判成"未提出"——只因段里出现了「无实证」；
    「明显的遗漏点」更离谱，乙的**标题**写着「（作者未看见的维度…）」，
    段首第一个词就是「作者未看见」。
    """
    assert cc._NEG_CODES == {"现有文献批评", "空白", "一致的研究发现",
                             "相反的研究发现", "未来研究建议"}
    # ★样本里的否定词要**能被 `_NEG` 命中**，否则测不到白名单本身：
    #   写「无实证数据」不会命中（词表里是「无实证发现」），有没有白名单都通过。
    card = ("| 研究结果 | 作者未报告任何数据；无实证发现 |\n"
            "### 明显的遗漏点（作者未看见的维度，与作者自陈局限分开写）\n"
            "1. 协同机制缺位，全文未讨论教师分工。\n")
    assert cc.judge(card, "研究结果")[0] == "有", "白名单失效：评价用语把「研究结果」判成了未提出"
    assert cc.judge(card, "明显的遗漏点")[0] == "有", "标题里的「作者未看见」不该影响本栏判定"


def test_ref_basis_reads_explicit_k():
    """回指基准：显式 K 优先，其次「PAGE n ↔ p.m」换算。"""
    assert cc.ref_basis("……K=+69，依据页脚裸数字。") == "K=+69"
    assert cc.ref_basis("PAGE 1 ↔ p.70，偏移见上。") == "K=+69"
    assert cc.ref_basis("回指基准：章节名。") == "章节名"


def test_main_requires_two_cards(tmp_path, capsys):
    """少于两份无从比对——口径一致性测的就是分歧。"""
    one = tmp_path / "a.md"
    one.write_text("# x\n", encoding="utf-8")
    assert cc.main([str(one)]) == 2


def test_main_reports_hard_disagreement(tmp_path, capsys):
    """★档位不同 → 硬分歧 → 退出码 1（可用于流水线）。"""
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    a.write_text("| 档位 | **T1 核心** |\n", encoding="utf-8")
    b.write_text("| 档位 | **T3 背景** |\n", encoding="utf-8")
    assert cc.main([str(a), str(b)]) == 1
    out = capsys.readouterr().out
    assert "不一致" in out and "T1核心" in out and "T3背景" in out


def test_main_passes_when_all_agree(tmp_path, capsys):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    body = ("| 档位 | **T2 重要** |\n| 空白 | **作者未提出空白** |\n"
            "| 作者 | 甲 |\n| 年份 | 2025 |\n")
    a.write_text(body, encoding="utf-8")
    b.write_text(body, encoding="utf-8")
    assert cc.main([str(a), str(b)]) == 0


def test_items_split_numbered_list_inside_a_table_cell():
    """★表格**单元格内连写**的编号条目也要切出来。

    实测（2026-10-09 重测）：读者把 5 条批评点压在一个单元格里
    （`| 批评点 | **1. … 2. … 3. …** |`），没有换行，行首锚点切不出多条，
    本脚本只读出 **1 条**——★这正是先前"先按行首编号"那一改的**镜像缺口**。
    """
    card = ("| 批评点 | **1. 核心构念无可操作性定义**：未给指标。 "
            "**2. 时点局限（最硬一条）**：2018 年文而 2025 版指南尚未存在。 "
            "**3. 论证跳跃**：以市场薪资推出中小学招不到教师。 |\n")
    got = cc.items(card, "批评点")
    assert len(got) == 3, f"单元格内连写只切出 {len(got)} 条：{got}"


def test_items_still_split_multiline_lists():
    """★改完之后，多行列表仍要照常切（别为了单元格把换行那条路径弄坏）。"""
    card = ("### 批评点\n\n1. 第一条批评，给了依据。\n2. 第二条批评，给了依据。\n"
            "3. 第三条批评，给了依据。\n4. 第四条批评，给了依据。\n")
    assert len(cc.items(card, "批评点")) == 4


def test_ref_basis_accepts_bare_number_sequence():
    """★回指基准兼容「各页页脚裸数字依次为 70/71/72」这类写法。

    实测（2026-10-09 第三轮）：一位读者写成「各页页脚裸数字依次为 70/71/72」——
    **信息完整、可复算**，只是没有 `K=` 这个可机读表达，比对脚本便判成"缺失"。
    ★判准侧已要求**必须写明 K**；但脚本侧**不该因此把完整信息判为缺失**。
    """
    assert cc.ref_basis("回指基准：各页页脚裸数字依次为 70/71/72") == "K=+69"
    assert cc.ref_basis("页脚裸数字依次为 70、71、72、73") == "K=+69"
    # 不含连续序列时不应乱推
    assert cc.ref_basis("回指基准：章节名") == "章节名"
    assert cc.ref_basis("回指基准：各页页脚裸数字依次为 70/99/12") not in ("K=+69",)
