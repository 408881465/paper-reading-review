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
