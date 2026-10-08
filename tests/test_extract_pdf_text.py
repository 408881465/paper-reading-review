"""extract_pdf_text 的清洗规则测试。

这些用例锁定的都是「静默毁掉证据」类的历史 bug——它们不抛异常，
只是悄悄删数字、改页码，最难在成稿后被发现。
"""

from conftest import extract_pdf_text as E


def test_bare_digits_are_kept_by_default():
    """表格里的年份/样本量/频数常独立成行，默认必须保留。"""
    raw = "表 3 样本分布\n2023\n城镇\n1245\n乡村\n867\n"
    out = E.clean_page_text(raw)
    for token in ("2023", "1245", "867"):
        assert token in out


def test_page_numbers_detected_by_offset():
    """页脚页码：位置在末行，且与页序保持恒定偏移。"""
    pages = [f"正文第{i + 1}页内容\n\n{i + 1}\n" for i in range(4)]
    found = E.detect_page_number_lines(pages)
    assert found == {(i, 1) for i in range(4)}


def test_offset_page_numbers_detected():
    """PDF 页序与印刷页码不一致（封面未编号）时，按偏移量识别。"""
    pages = [f"正文{i}内容\n\n{i}\n" for i in range(1, 6)]
    found = E.detect_page_number_lines(pages)
    assert found == {(i, 1) for i in range(5)}


def test_table_digits_are_not_mistaken_for_page_numbers():
    """每页末行都是同一个数字（如频数 867）时，不构成「随页序递增」的页码。"""
    pages = ["Table\n2023\n1245\n867\n" for _ in range(4)]
    assert E.detect_page_number_lines(pages) == set()


def test_detected_page_number_is_dropped():
    raw = "正文内容\n\n1\n"
    out = E.clean_page_text(raw, drop_positions={1})
    assert out == "正文内容"


def test_hyphen_break_does_not_join_digit_ranges():
    """旧实现把「pp. 12-\\n15」并成「pp. 1215」，毁掉回指用的页码。"""
    out = E.clean_page_text("参见 pp. 12-\n15 的数据\n")
    assert "12-" in out and "15" in out
    assert "1215" not in out
    assert "20192020" not in E.clean_page_text("2019-\n2020 年\n")


def test_hyphen_break_still_joins_words():
    assert "cooperation" in E.clean_page_text("cooper-\nation works\n")


def test_urls_are_preserved():
    """参考文献的 DOI/URL 是回查证据，不能当噪声删掉。"""
    raw = "参考文献\n[1] 张三. 研究[J]. 期刊, 2020.\nhttps://doi.org/10.1234/abcd\n"
    assert "https://doi.org/10.1234/abcd" in E.clean_page_text(raw)


def test_header_noise_is_removed():
    raw = "第 3 页\n© 2020 出版社\nAll rights reserved\n正文内容\n"
    assert E.clean_page_text(raw) == "正文内容"


def test_paragraph_structure_is_preserved():
    raw = "第一段第一句。\n第一段第二句。\n\n\n\n第二段。\n"
    assert E.clean_page_text(raw) == "第一段第一句。\n第一段第二句。\n\n第二段。"


# ---------------------------------------------------------------- 页码行判据（真实文献实测）
def test_page_number_value_accepts_chinese_footer_decoration():
    """★中国学术期刊页脚是 `— 146`，不是纯数字。

    实测 L0186 各页页脚为 `— 146`／`— 147`／`— 148`…、L0102 为 `— 147` 起，
    偏移恒定（+145／+146）。旧判据 `^\\s*\\d{1,4}\\s*$` 全部拒收 →
    整篇不做印刷页码标注，而"证据可回指"正依赖它。
    """
    for line, val in [("— 146", 146), ("-147", 147), ("· 148", 148),
                      ("[149]", 149), ("第 150 页", 150), ("  151  ", 151)]:
        assert E.page_number_value(line) == val, f"{line!r} 应判为页码 {val}"
    for line in ["Page 12 of 17", "2020.12", "—", "第 146 节", "16—06", "正文"]:
        assert E.page_number_value(line) is None, f"{line!r} 不应判为页码"


def test_western_page_footer_is_deliberately_not_accepted():
    """`Page 12 of 17` 标的是文档内页序，不是印刷页码——刻意不认。

    是否一致需要外部信号判断，而 batch_extract 的 help 明确
    "没有外部验证就不要标注"；返回 None（=不标注）是安全结果。
    """
    assert E.page_number_value("Page 12 of 17") is None
    assert E.page_number_value("Page 12") is None
