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
    assert any("提问与作答必须分开" in w for w in res["warnings"])


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
    # 3000 字：T2 卡超限（warning），章节级卡合格
    text = "# 章节级解码卡\n\n" + "研究结果与批评点。" * 350   # 约 2800 汉字
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
