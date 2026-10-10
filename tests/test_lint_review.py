"""lint_review 的规则测试。

每条规则都要有「该拦的拦住」和「不该拦的别误报」两侧用例——
自检脚本一旦误报，人就会开始忽略它，比没有还糟。
"""

import pytest

import lint_review as lr


def _codes_block():
    return "\n".join(f"- {name}：示例内容（p.{i + 1}）"
                     for i, name in enumerate(lr.CODE_NAMES))


def test_counts_cjk_plus_english_words():
    assert lr.count_cjk("人工智能 education") == 4 + 1
    assert lr.count_cjk("") == 0


def test_bare_abbreviation_is_error():
    res = lr.lint_text("x.md", "# 标题\n\n本文用 ROF 表示研究结果。\n", form="B",
                       min_chars=0, max_chars=10 ** 9)
    assert not res["ok"]
    assert any("纯缩写残留" in e for e in res["errors"])


def test_parenthesised_abbreviation_is_allowed():
    res = lr.lint_text("x.md", "# 研究结果（ROF）\n\n内容。\n", form="B",
                       min_chars=0, max_chars=10 ** 9)
    assert not any("纯缩写残留" in e for e in res["errors"])


def test_ideographic_bracket_abbreviation_is_allowed():
    """★中文名**自身含（）**时，缩写括注按规范改用〔〕，同样不得判为裸缩写。

    2026-10-10 改名的直接后果：`研究结果（作者发现了什么）〔ROF〕` 是**规范写法**，
    但括注判定若只认 `（）`，向左扫到的是 `〔`，会一路扫到行首 → 判成裸缩写，
    **照规范写反而被罚**（与 2026-10-08 那批 ERROR 同类）。
    """
    text = ("# 研究结果（作者发现了什么）〔ROF〕\n\n"
            "理论依据（研究的必要性）〔RAT〕见上。\n")
    res = lr.lint_text("x.md", text, form="B", min_chars=0, max_chars=10 ** 9)
    assert not any("纯缩写残留" in e for e in res["errors"]), res["errors"]


def test_table_abbreviation_warns_but_does_not_block():
    text = "# 表\n\n| 项 | 含义 |\n|---|---|\n| ROF | 研究结果 |\n"
    res = lr.lint_text("x.md", text, form="B", min_chars=0, max_chars=10 ** 9)
    assert res["ok"]
    assert any("表格内纯缩写" in w for w in res["warnings"])


def test_fenced_code_block_is_skipped():
    text = "# 示例\n\n```\nROF SPL GAP\n```\n"
    res = lr.lint_text("x.md", text, form="B", min_chars=0, max_chars=10 ** 9)
    assert not any("纯缩写残留" in e for e in res["errors"])


def test_banned_phrases_block():
    for word, _why in lr.BANNED:
        res = lr.lint_text("x.md", f"# 判定\n\n该文{word}。\n", form="B",
                           min_chars=0, max_chars=10 ** 9)
        assert not res["ok"], word
        assert any("禁用表述" in e for e in res["errors"])


def test_char_range_enforced():
    res = lr.lint_text("x-导读.md", "# 短\n\n内容。\n", form="A",
                       min_chars=1500, max_chars=10000)
    assert any("低于" in e for e in res["errors"])
    res = lr.lint_text("x-导读.md", "字" * 20000, form="A")
    assert any("超过" in e for e in res["warnings"])


def test_gap_without_theory_is_error_and_with_theory_passes():
    bad = "# 空白\n\n现有研究未涉及协同机制。\n\n# 结论\n\n略。\n"
    res = lr.lint_text("x.md", bad, form="B", min_chars=0, max_chars=10 ** 9)
    assert any("理论依据" in e for e in res["errors"])

    good = ("# 空白\n\n现有研究未涉及协同机制。\n\n"
            "# 理论依据\n\n活动理论可用以解释协同。\n")
    res = lr.lint_text("x.md", good, form="B", min_chars=0, max_chars=10 ** 9)
    assert not any("理论依据" in e for e in res["errors"])


def test_laundry_list_warning():
    names = ["张三", "李四", "王五", "赵六", "钱七", "孙八", "周九", "吴十"]
    paras = [f"{name}（20{10 + i}）指出协同机制很重要。" for i, name in enumerate(names)]
    res = lr.lint_text("x.md", "# 综述\n\n" + "\n\n".join(paras), form="B",
                       min_chars=0, max_chars=10 ** 9)
    assert any("洗衣店接衣单" in w for w in res["warnings"])


def test_topic_organised_text_has_no_laundry_warning():
    paras = ["协同机制在组织层面表现为跨部门联动，已有研究多止于描述。",
             "在教师层面，协同被理解为跨学科备课共同体的日常运作。"]
    res = lr.lint_text("x.md", "# 综述\n\n" + "\n\n".join(paras), form="B",
                       min_chars=0, max_chars=10 ** 9)
    assert not any("洗衣店接衣单" in w for w in res["warnings"])


def test_locator_density_warning():
    res = lr.lint_text("x.md", "# 综述\n\n" + "研究结果很多。" * 200, form="B",
                       min_chars=0, max_chars=10 ** 9)
    assert any("回指" in w for w in res["warnings"])


def test_form_a_requires_all_codes():
    res = lr.lint_text("x-导读.md", "# 残缺\n\n" + "字" * 2000, form="A")
    assert any("缺密码栏" in e for e in res["errors"])

    full = "# 单篇导读\n\n" + _codes_block() + "\n" + "字" * 2000
    res = lr.lint_text("x-导读.md", full, form="A")
    assert not any("缺密码栏" in e for e in res["errors"])


def test_guess_form_by_filename():
    assert lr.guess_form("a-速览.md", "") == "quick"
    assert lr.guess_form("a-导读.md", "") == "A"
    assert lr.guess_form("主题-文献综述.md", "") == "B"
    assert lr.guess_form("A-B对比评述.md", "") == "C"
    assert lr.guess_form("L001-解码卡.md", "") == "card"
    assert lr.guess_form("文献阅读总报告.md", "") == "report"


def test_merged_question_answer_warns():
    text = "# 综述\n\n张三（2020）研究了校本课程并发现协同有效。\n"
    res = lr.lint_text("x.md", text, form="B", min_chars=0, max_chars=10 ** 9)
    assert any("承诺与交付必须分开" in w for w in res["warnings"])


def test_cli_exit_codes(tmp_path, capsys):
    good = tmp_path / "主题-文献综述.md"
    good.write_text("# 综述\n\n" + "主题化论述，按机制层面重组文献。" * 80 + "\n", encoding="utf-8")
    assert lr.main([str(good)]) == 0

    bad = tmp_path / "坏.md"
    bad.write_text("# 判定\n\n该文有一定参考价值。\n", encoding="utf-8")
    assert lr.main([str(bad)]) == 1
    assert "error" in capsys.readouterr().out


def test_json_output(tmp_path, capsys):
    f = tmp_path / "主题-文献综述.md"
    f.write_text("# 综述\n\n内容。\n", encoding="utf-8")
    lr.main([str(f), "--json"])
    assert '"form"' in capsys.readouterr().out


def test_doc_form_does_not_flag_quoting_docs():
    """规范文档必须能引用禁用词与缩写来讲规则，不该被自己的规则拦下。"""
    text = ("# 规则\n\n禁止写「方法有待加强」；密码缩写（WTD）仅作首次括注。\n"
            "正文中出现 ROF 才算残留。\n")
    res = lr.lint_text("references/x.md", text, form="auto")
    assert res["form"] == "doc"
    assert not any("禁用表述" in e for e in res["errors"])
    # doc 形态下仍保留缩写残留检查（这条对说明文档同样有意义）
    assert any("纯缩写残留" in e for e in res["errors"])


def test_skip_turns_off_selected_rules():
    res = lr.lint_text("x.md", "# 判定\n\n该文有一定参考价值。\n", form="B",
                       min_chars=0, max_chars=10 ** 9, skip=["banned"])
    assert not any("禁用表述" in e for e in res["errors"])


def test_unknown_rule_name_raises():
    with pytest.raises(ValueError):
        lr.lint_text("x.md", "内容", form="B", skip=["nonexistent"])


def test_chapter_card_has_its_own_range():
    """回归：>60 页专著/学位论文的章节级卡不能套 T2 卡的 2500 字上限。"""
    assert lr.FORM_RANGES["chapter-card"] == (400, 5000)
    assert lr.guess_form("L0150_王新燕-章节解码卡.md", "") == "chapter-card"
    assert lr.guess_form("L0022-Kim-解码卡.md", "") == "card"
    # ★一页卡上限 2026-10-09 由 2500 放宽到 3200（按 78 份真实卡标定），
    #   故样本要取到 3200 以上，才仍能验证「T2 卡超限、章节级卡合格」。
    text = "# 章节级解码卡\n\n" + "研究结果与批评点。" * 450   # 约 2800 汉字
    res_card = lr.lint_text("x-解码卡.md", text, form="card")
    assert any("超过" in w for w in res_card["warnings"])
    res_chapter = lr.lint_text("x-章节解码卡.md", text, form="chapter-card")
    assert not any("超过" in w for w in res_chapter["warnings"])


def test_allow_abbr_whitelists_domain_homographs():
    """回归：RPP 既可能是密码缩写，也可能是 Research–Practice Partnership 领域术语。"""
    # 首次括注合规，第二处是裸缩写——后者才是要拦的对象
    text = "# 综述\n\n研究—实践伙伴关系（RPP）是本文的分析单位；RPP 强调长期协作。\n"
    res = lr.lint_text("x.md", text, form="B", min_chars=0, max_chars=10 ** 9)
    assert any("纯缩写残留" in e for e in res["errors"])      # 默认仍会拦
    res = lr.lint_text("x.md", text, form="B", min_chars=0, max_chars=10 ** 9,
                       allow_abbr=["RPP"])
    assert not any("纯缩写残留" in e for e in res["errors"])   # 白名单后放行


def test_chapter_word_in_ordinary_filename_is_not_a_chapter_card():
    """回归：「研究现状章节素材.md」讲的是"章节素材"，不是章节级解码卡。"""
    assert lr.guess_form("08_课题素材/研究现状章节素材.md", "") != "chapter-card"
    assert lr.guess_form("L0150_王新燕-章节解码卡.md", "") == "chapter-card"


# ---------------------------------------------------------------- hygiene
# 2026-10-08：自动化改写留下的两类垃圾，人眼极难发现（详见 references/batch-workflow.md §8.4）

def _hygiene_errors(text, form="B", **kw):
    res = lr.lint_text("x.md", text, form=form, min_chars=0, max_chars=10 ** 9, **kw)
    return [e for e in res["errors"] if "HTML 实体" in e or "字面泄漏" in e]


def test_html_entity_is_error():
    """脚本把非 ASCII 转义成 &#x7684; 后未回转，会静默混进正文。"""
    errs = _hygiene_errors("# 标题\n\n这里有个&#x7684;实体残留。\n")
    assert any("HTML 实体残留" in e and "&#x7684;" in e for e in errs)


def test_backslash_reference_leak_is_error():
    """re.sub 替换体写成 r\"\\1\"+text（而非函数）时，\\1 被当字面量输出。"""
    errs = _hygiene_errors("# 标题\n\n\\1协同过程指标观测表\n")
    assert any("反斜杠引用字面泄漏" in e for e in errs)


def test_backslash_leak_tight_against_chinese_is_caught():
    """★关键回归：Python 的 \\w 包含中文，用 \\w 做前后判据会整类漏掉
    「\\1 紧贴汉字」——而这恰是泄漏最典型的形态（本规则第一版就栽在这里）。"""
    for text in ("# 标题\n\n\\1协同过程指标观测表\n",   # \\1 后紧贴汉字
                 "# 标题\n\n标题\\1内容\n",             # 前后都紧贴汉字
                 "# 标题\n\n\\1 有空格\n"):             # 独占行首
        assert _hygiene_errors(text), f"未检出：{text!r}"


def test_inline_code_and_fence_are_exempt_from_hygiene():
    """正则讨论写在代码里是正当用法，不得假警报
    （假警报会让整份 lint 输出被无视，等于没有 lint）。"""
    text = ('# 标题\n\n正确写法是 `re.sub(r"(\\w)", r"\\1", s)`。围栏里也一样：\n\n'
            "```python\nre.sub(r\"(\\w)\", r\"\\1\", s)\n```\n")
    assert not _hygiene_errors(text)


def test_doc_form_still_checks_hygiene():
    """★hygiene 不受 doc 豁免：污染在任何形态下都是错误，与"文档要引用禁用词"不同。"""
    res = lr.lint_text("references/x.md", "# 标题\n\n污染&#x4EE5;。\n",
                       form="doc", min_chars=0, max_chars=10 ** 9)
    assert any("HTML 实体残留" in e for e in res["errors"])


def test_skip_hygiene_turns_it_off():
    res = lr.lint_text("x.md", "# 标题\n\n污染&#x4EE5;。\n", form="B",
                       min_chars=0, max_chars=10 ** 9, skip=["hygiene"])
    assert not [e for e in res["errors"] if "HTML 实体" in e]


# ---------------------------------------------------------------- 局限的归属
# 2026-10-08 用真实文献跑形态 A 时发现：这条规则原本**一个测试都没有**，
# 于是改写它时丢掉 `not _PRAISE` 条件都没人拦。

def _limits_warned(text):
    res = lr.lint_text("x.md", text, form="A", min_chars=0, max_chars=10 ** 9)
    return any("局限" in w for w in res["warnings"])


def test_unattributed_limitation_warns():
    """归属不明的「局限」+ 全文无「本文评述」→ 应当告警。"""
    assert _limits_warned("# 导读\n\n本文讨论了该研究的局限。\n")


def test_limitation_with_praise_marker_passes():
    """★模板的小节标题「五、本文贡献与局限」本身就含该词。
    只要全文有「本文评述」，就不该报——否则每一份按模板写的导读都会中招。"""
    text = ("# 导读\n\n## 五、本文贡献与局限\n\n"
            "本文评述认为，该研究的方法存在样本偏小的局限。\n")
    assert not _limits_warned(text)


def test_attributed_limitation_without_praise_passes():
    """「作者未自陈局限」已把归属写明，本身就是合规写法，不该报。"""
    assert not _limits_warned("# 速览\n\n它未提出空白、未自陈局限。\n")


# ---------------------------------------------------------------- 并列括注
# 2026-10-08 用真实文献跑形态 A 时实测：模板标题写「（WTD / WTDD）」「（SPL / CPL）」
# 「（MOP / RPP）」，而旧实现只看紧邻前一个字符，把第二个缩写判成裸缩写——
# **照模板写出的成稿必然报 3 处 ERROR**。

def _abbr_errors(text):
    res = lr.lint_text("x.md", text, form="A", min_chars=0, max_chars=10 ** 9)
    return [e for e in res["errors"] if "纯缩写" in e]


def test_paired_parenthetical_abbreviations_are_allowed():
    """并列括注里的**每一个**缩写都算首次括注，不只是第一个。"""
    for s in ("（WTD）", "（WTD / WTDD）", "（SPL / CPL）", "（MOP / RPP）",
              "（研究结果 / 理论依据）"):
        assert not _abbr_errors(f"# x\n\n### a {s}\n\n正文。\n"), f"{s} 被误判"


def test_abbreviation_after_closed_paren_is_still_bare():
    """括注**之外**的缩写仍须报——`见（表 1）ROF 的说明` 里 ROF 不算括注用法。"""
    assert _abbr_errors("# x\n\n见（表 1）ROF 的说明。\n")


def test_bare_abbreviation_in_prose_still_caught():
    """正文行文里的裸缩写照旧要报（不能为了修并列括注而放走它）。"""
    assert _abbr_errors("# x\n\n本文用 ROF 表示研究结果。\n")


# ---------------------------------------------------------------- 用真实文献实测后补的规则精度
# 2026-10-08 拿真实文献跑形态 A/B/C/D，发现以下四条规则会把**合规产出**判成违规。

def test_html_comment_is_not_content():
    """★注释不是正文。模板通篇是 `<!-- 填写说明 -->`，把注释当内容校验，
    等于拿"给填写者的指引"去判"成稿是否合规"。"""
    text = "# x\n\n<!-- 说明：WTD = 作者要做什么；禁止「方法有待加强」 -->\n\n正文。\n"
    res = lr.lint_text("x.md", text, form="A", min_chars=0, max_chars=10 ** 9)
    assert not [e for e in res["errors"] if "纯缩写" in e]
    assert not [e for e in res["errors"] if "禁用表述" in e]


def test_banned_word_quoted_in_rule_is_not_a_violation():
    """★规则文档必须**引用**禁用词才能讲清规则（「禁止『方法有待加强』」）。
    不区分使用与提及，会让模板满屏假警报；而假警报会让人忽略 lint。"""
    ok = "# x\n\n批评点必须落在具体处。禁止「方法有待加强」这类空话。\n"
    res = lr.lint_text("x.md", ok, form="card", min_chars=0, max_chars=10 ** 9)
    assert not [e for e in res["errors"] if "禁用表述" in e]

    bad = "# x\n\n该研究方法有待加强。\n"
    res = lr.lint_text("x.md", bad, form="card", min_chars=0, max_chars=10 ** 9)
    assert [e for e in res["errors"] if "禁用表述" in e], "真正的使用仍须拦住"


def test_gap_rationale_accepts_skill_sanctioned_phrasing():
    """★「空白必配理论依据」里的依据，技能认可的写法有两种：
    直呼「理论依据」，或写成「因此可开展的研究是……」（SKILL.md 第 3 步原话）。
    只认前者会把**按模板写好的稿子**判成违规。"""
    t = ("# 四、文献的批评与空白（作者对现有文献的批评 / 空白）\n\n"
         "### 4.2 系统性研究空白\n\n#### 空白 1：x\n\n"
         "- 提出该空白的文献：甲\n- **因此可开展的研究是**：补做 y\n")
    res = lr.lint_text("x.md", t, form="B", min_chars=0, max_chars=10 ** 9)
    assert not [e for e in res["errors"] if "理论依据" in e]


def test_gap_check_covers_subtree_not_only_next_section():
    """★父节标题含"空白"、配依据的是它的**子节**——只看"本节与下一节"会误判。
    实测：多篇模板的「四、文献的批评与空白」正是这个结构。"""
    t = ("# 四、文献的批评与空白\n\n概述。\n\n"
         "## 4.1 反复出现的批评点\n\n甲、乙。\n\n"
         "### 4.2 系统性研究空白\n\n**理论依据**：活动理论可解释。\n")
    res = lr.lint_text("x.md", t, form="B", min_chars=0, max_chars=10 ** 9)
    assert not [e for e in res["errors"] if "理论依据" in e]


def test_locator_counts_section_name_fallback():
    """★batch-workflow §5.1 第 2 条：**没有页码标注就回指章节名**。
    校验器不认章节名回指的话，按 §5.1 合规写的稿子会被警告"可回指 0 处"
    （实测：L0041 页码抽坏、L0074 无页码，只能章节名回指）。"""
    t = ("# 综述\n\n见「研究背景」一节；另见第 二 节与 p.147。\n" + "正文。" * 400)
    res = lr.lint_text("x.md", t, form="B", min_chars=0, max_chars=10 ** 9)
    assert res["locators"] >= 3, f"章节名回指未计入可回指标记（得 {res['locators']}）"


# ---------------------------------------------------------------- 命名实体（真实语料查出）
def test_named_html_entities_are_caught():
    """★只认数字实体会漏掉命名实体。

    实测：某政策文件 .docx 的旧版抽取结果里有 **54 处 `&quot;`**、1 处 `&gt;`，
    而旧的 `&#x?[0-9A-Fa-f]{2,6};` 只匹配数字实体，整类漏检。
    """
    for ent in ("&quot;", "&amp;", "&lt;", "&gt;", "&nbsp;", "&ldquo;"):
        res = lr.lint_text("x.md", f"# x\n\n污染 {ent} 残留。\n", form="B",
                           min_chars=0, max_chars=10 ** 9)
        assert any("HTML 实体" in e for e in res["errors"]), f"{ent} 漏检"


def test_lone_ampersand_is_not_an_entity():
    """不能为了抓实体而误伤正文里的 `&` 与 `R&D`（假警报会让人忽略 lint）。"""
    res = lr.lint_text("x.md", "# x\n\nA & B 合作，R&D 投入。\n", form="B",
                       min_chars=0, max_chars=10 ** 9)
    assert not [e for e in res["errors"] if "HTML 实体" in e]


# ---------------------------------------------------------------- 章节卡的形态识别
# 2026-10-08 用 137 份真实历史产出跑规模 lint 时查出：
# 同一份 3417 字的内容，文件名带「章节」→ chapter-card（上限 5000）通过；
# 去掉「章节」→ card（上限 2500），报「超限 917 字」。**内容一字未改。**

def test_chapter_card_detected_from_filename():
    assert lr.guess_form("L0150-王新燕-章节解码卡.md", "") == "chapter-card"
    assert lr.guess_form("L0068-红皮书章节卡.md", "") == "chapter-card"


def test_chapter_card_detected_from_content_when_filename_is_free_form():
    """★文件名是自由的；字数区间却差一倍，故必须有内容特征兜底。

    真实章节卡的 H1 用「章节级解码卡」（抽查 20 份一页卡，含此特征的 0 份）。
    """
    body = "# 章节级解码卡（形态 D · 专著／学位论文用）\n\n正文。\n"
    assert lr.guess_form("L0150-王新燕-美国中小学工程教育.md", body) == "chapter-card"


def test_plain_card_is_not_mistaken_for_chapter_card():
    """一页卡不得因内容兜底被误判成章节卡（那会放宽上限一倍）。"""
    body = "# 单篇解码卡（形态 D · T2／专著章节用）\n\n| 项目 | 内容 |\n"
    assert lr.guess_form("L0179-Cook-协同教学模式-解码卡.md", body) == "card"


def test_same_content_same_verdict_regardless_of_filename():
    """★核心断言：同一份内容，命名不同不得导致一个通过、一个超限。"""
    body = "# 章节级解码卡（形态 D · 专著／学位论文用）\n\n" + "正文内容。" * 1200
    for name in ["L0150-章节解码卡.md", "L0150-解码卡.md", "L0150-随便什么名.md"]:
        assert lr.guess_form(name, body) == "chapter-card", name
        res = lr.lint_text(name, body, form="auto", min_chars=0, max_chars=10 ** 9)
        assert not [e for e in res["errors"] if "字数" in e], f"{name} 被误报超限"


# ---------------------------------------------------------------- 一页卡的内容识别
# 2026-10-09 用形态 D 端到端跑批时查出：产出命名是自由的，按 `T2-L0110-作者-主题.md`
# 这样命名很自然，文件名不含"解码卡"；而卡正文含「作者要做什么」，
# 于是落到结构兜底被判成 A —— 一页卡被套上详版的 1500 字下限，
# 三张卡（1017／1129／1392 字）全部报"低于下限"。

def test_card_detected_from_content_when_filename_is_free_form():
    """★一页卡的正文特征：阿拉伯数字小节（`## 0. 题录` / `## 3. 与本课题的接口`）。

    与详版模板的中文数字小节（`## 一、导读摘要`）不混。
    """
    body = ("# 单篇解码卡\n\n## 0. 题录\n\n| 项目 | 内容 |\n|---|---|\n"
            "| 编号 | L0110 |\n\n## 1. 结构性密码\n\n"
            "| 密码 | 内容 |\n|---|---|\n| 作者要做什么 | x |\n\n"
            "## 2. 策略性密码\n\n## 3. 与本课题的接口\n")
    # 文件名不含"解码卡"
    assert lr.guess_form("T2-L0110-杨鹏-计算思维模型.md", body) == "card"
    res = lr.lint_text("T2-L0110-杨鹏-计算思维模型.md", body,
                       form="auto", min_chars=0, max_chars=10 ** 9)
    assert not [e for e in res["errors"] if "字数" in e], "一页卡被套上了详版的字数下限"


def test_full_review_not_mistaken_for_card():
    """详版模板用中文数字小节，不得被内容兜底误判成 card。"""
    body = ("# 单篇深度导读\n\n## 一、导读摘要\n\n## 二、结构性密码\n\n"
            "| 密码 | 内容 |\n|---|---|\n| 作者要做什么 | x |\n\n"
            "## 三、策略性密码\n\n## 四、本文评述的判断\n")
    assert lr.guess_form("T1-L0030-作者-主题.md", body) == "A"


def test_report_detected_from_content_when_filename_is_free_form():
    """★第三处"形态判定靠文件名"的补丁。

    2026-10-09 实测：把总报告按自由命名（去掉"总报告"三字）后判成 `B`，
    于是套上 B 的 800–20000 区间而非 report 的 1200–∞——**检查被放松**。
    总报告模板有两处别处不会出现的节名，可作稳定特征。
    """
    body = ("# 跨学科协同批次\n\n## 一、覆盖与精读声明\n\n| 项 | 数量 |\n|---|---|\n"
            "| T1 | 3 |\n\n## 二、主题格局\n\n## 五、一致性三查结果\n\n| 查项 | 结论 |\n")
    assert lr.guess_form("跨学科协同批次.md", body) == "report"


def test_other_forms_not_mistaken_for_report():
    """B/C/A 不得被报告特征误判。"""
    b = "# 主题综述：x\n\n## 一、综述摘要\n\n## 三、现有文献的主题格局\n"
    assert lr.guess_form("跨学科协同研究.md", b) == "B"
    a = "# 单篇深度导读：x\n\n## 一、引用信息\n\n## 二、结构性密码\n\n| 作者要做什么 | y |\n"
    assert lr.guess_form("某篇研究.md", a) == "A"


# ═══════════════════════════════════════════════════════════════════════════════
# 形态判定根治：**内容特征优先，文件名退居兜底**
# 旧实现以文件名为第一判据，同一类缺陷出现过三次（章节卡／一页卡／总报告），
# 每次都靠加内容兜底打补丁。2026-10-09 根治：把内容特征提到文件名之前。
# ═══════════════════════════════════════════════════════════════════════════════

_TEMPLATES = {
    "single-review-template.md": "A",
    "quick-review-template.md": "quick",
    "multi-review-template.md": "B",
    "comparative-review-template.md": "C",
    # ★这是 **T2 一页卡**模板（H1 是「单篇解码卡（形态 D · T2／专著章节用）」）——
    #   章节卡的专属标记「章节级解码卡」只出现在它内部的**注释**里（给填写者的说明），
    #   而签名只匹配标题行，故判 `card` 是对的。章节卡由 `--form chapter-card`
    #   或文件名/首行含「章节」识别。
    "decode-card-template.md": "card",
    "report-template.md": "report",
}


def test_every_template_is_identified_by_its_own_content():
    """★六套模板的正文特征必须各自唯一命中——这是"内容优先"能成立的前提。

    用 assets/ 之外的路径判定，否则会被 `doc` 规则接走。
    探针实测：六套模板各只命中自己（章节卡模板同时含卡特征，靠**顺序**取胜）。
    """
    import os
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(lr.__file__))), "assets")
    for fn, want in _TEMPLATES.items():
        text = open(os.path.join(root, fn), encoding="utf-8").read()
        got = lr.guess_form(fn, text)
        assert got == want, f"{fn} 内容判定为 {got}，应为 {want}"


def test_content_wins_over_filename():
    """★根治的核心断言：内容与文件名冲突时，**以内容为准**。

    旧实现下这三例都会被文件名带偏，导致按错的规格检查。
    """
    # 名叫"解码卡"但内容其实是详版 → 判 A
    a_body = ("# 单篇深度导读\n\n## 一、导读摘要\n\n## 五、本文贡献与局限\n\n## 六、读后总评\n")
    assert lr.guess_form("某篇-解码卡.md", a_body) == "A"
    # 名叫"总报告"但内容其实是主题综述 → 判 B
    b_body = "# 主题综述：x\n\n## 一、综述摘要\n\n## 二、文献范围与筛选说明\n"
    assert lr.guess_form("总报告.md", b_body) == "B"
    # 自由命名、内容是章节卡 → 判 chapter-card（章节卡优先于卡）
    ch_body = ("# 章节级解码卡（形态 D）\n\n## 0. 题录\n\n## 1. 结构性密码\n\n"
               "## 2. 策略性密码\n\n## 3. 与本课题的接口\n")
    assert lr.guess_form("随便什么名.md", ch_body) == "chapter-card"


def test_filename_still_works_when_content_is_uninformative():
    """内容判不出时，文件名兜底必须还在（否则空文件/摘录片段会全落到 B）。"""
    assert lr.guess_form("某篇-速览.md", "") == "quick"
    assert lr.guess_form("某主题-对比评述.md", "") == "C"
    assert lr.guess_form("某主题-综述.md", "") == "B"
    assert lr.guess_form("某篇-导读.md", "") == "A"
    assert lr.guess_form("某篇-章节解码卡.md", "") == "chapter-card"


def test_card_upper_bound_is_calibrated_to_real_output():
    """★一页卡上限按真实产出标定：2500 → 3200。

    依据：78 份真实解码卡的分布 min 1222／中位 2125／P75 2416／**P90 2799**／max 3088。
    2500 切在 P75 与 P90 之间，**19%（14/73）的正常产出触发**；且核对发现其中
    13 份已按规则要求标注了字数与取舍——说明是上限标定不符，不是产出习惯问题。
    """
    assert lr.FORM_RANGES["card"] == (200, 3200)
    # 实测最大值 3088 应不再触发
    body = "# 单篇解码卡\n\n## 0. 题录\n\n## 1. 结构性密码\n\n## 2. 策略性密码\n\n" + "内容。" * 1550
    res = lr.lint_text("x-解码卡.md", body, form="auto", min_chars=0, max_chars=10 ** 9)
    assert not [w for w in res["warnings"] if "超过 card" in w], "实测最大值仍在报警"


def test_script_generated_extract_report_is_not_a_review_doc():
    """★脚本自己生成的报告不是 review 成稿。

    实测（2026-10-09）：对工作区做**全库 lint** 时，`batch_extract` 生成的
    `_提取报告.md`（27 字）被判成 B 形态，报"字数 27 低于 B 形态下限 800"——
    于是"全库体检"一片红，而它根本不是人写的 review。
    与 `DOC_FORM_SKIP` 处理模板是同一类问题：**别把工具自己的产出当成人稿检查**。
    """
    body = "# 批量抽文本报告\n\n- 本次处理：2 篇\n- 成功：2\n- 需 OCR：0\n"
    assert lr.guess_form("_提取报告.md", body) == "doc"
    assert lr.guess_form("02_文本/_提取报告.md", body) == "doc"
    res = lr.lint_text("02_文本/_提取报告.md", body, form="auto")
    assert res["errors"] == [] and res["warnings"] == []


def test_form_a_requires_all_fourteen_codes_including_strategy_codes():
    """★A 形态必须齐备**全部 14 个**密码（10 结构 + 4 策略）。

    旧实现写的是 `CODE_NAMES[:10]`——只查前 10 个，于是详版可以完全不给
    「批评点／明显的遗漏点／待探讨的相关问题／逻辑能否走通（能否自圆其说）」而 lint 报 OK。
    这 4 个策略密码恰是技能的核心卖点（`reading-codes.md`：「读出作者**没**写什么」），
    `review-workflow.md` 自检清单也要求「十个结构性密码与四个策略性密码已逐项覆盖」，
    A 模板本身就有对应小节。

    2026-10-09 用真实中文实证文测 A 形态时查出：稿子确实缺这 4 个密码名，
    **而 lint 报「OK 未发现问题」**。影响面已核：24 份历史 A 形态导读全部齐备 14 个。
    """
    ten = "\n\n".join(f"## {n}\n\n内容。" for n in lr.CODE_NAMES[:10])
    body = "# x\n\n## 一、导读摘要\n\n" + ten
    res = lr.lint_text("x-导读.md", body, form="A", min_chars=0, max_chars=10 ** 9)
    assert [e for e in res["errors"] if "缺密码栏" in e], "只给 10 个密码竟被判合格"

    allc = "\n\n".join(f"## {n}\n\n内容。" for n in lr.CODE_NAMES)
    body2 = "# x\n\n## 一、导读摘要\n\n" + allc
    res2 = lr.lint_text("x-导读.md", body2, form="A", min_chars=0, max_chars=10 ** 9)
    assert not [e for e in res2["errors"] if "缺密码栏" in e], "14 个齐备却被报缺"


def test_comparative_review_without_matrix_is_flagged():
    """★对比评述删掉「对比矩阵」必须报警——它是本形态的定义性结构。

    2026-10-09 实测：删掉整节「二、对比矩阵」（878 字符）后 lint 仍报 OK——
    因为 C 的内容签名需命中 2 处，删掉矩阵后还剩「共识与分歧」「对比综述」，
    **仍判 C**，而 C 没有任何结构检查。

    ★只加这一条，**不**给 B/C 加"必填节"检查：实测 137 份历史产出中，
    主题3 把结构重组成"三问"（并把必填节名括注在标题里）、主题4 用
    "争议主轴／可比维度对照"，都是**合法的重组**，刚性检查会误伤。
    而「对比矩阵」是 SKILL.md 形态表写明的 C 形态定义特征。
    """
    with_matrix = ("# 对比评述：x\n\n## 一、对比综述\n\n## 二、对比矩阵\n\n| 维度 | A | B |\n|---|---|---|\n"
                   "| 研究问题 | | |\n\n## 六、共识与分歧\n\n## 七、方法论评价\n")
    res = lr.lint_text("x-对比评述.md", with_matrix, form="C", min_chars=0, max_chars=10 ** 9)
    assert not [w for w in res["warnings"] if "对比矩阵" in w], "有矩阵却报缺"

    without = with_matrix.replace("## 二、对比矩阵\n\n| 维度 | A | B |\n|---|---|---|\n| 研究问题 | | |\n\n", "")
    res2 = lr.lint_text("x-对比评述.md", without, form="C", min_chars=0, max_chars=10 ** 9)
    assert [w for w in res2["warnings"] if "对比矩阵" in w], "缺矩阵竟未报警"
