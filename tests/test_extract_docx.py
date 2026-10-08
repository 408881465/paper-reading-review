"""extract_docx_text 的行为测试。

三条断言都对应真实语料里查出来的缺陷（2026-10-08，本课题 4 份政策 .docx）：

1. **必须跳过 `w:instrText`（域指令）**——否则目录域会漏出
   `TOC \\o "1-3"`、`PAGEREF _Toc235530059`（实测某文件 17 处 PAGEREF + 17 处 HYPERLINK）。
2. **必须解码 XML 实体**——按字符串读 `document.xml` 会漏出 `&quot;`
   （实测某文件 54 处）。
3. **不得把 `pStyle` 的裸数字当标题级别**——中文 Word 模板里这类样式常是正文变体，
   实测某政策文件 70 段的 `pStyle` 全是 `"3"` 且无 `outlineLvl`，
   若把 `"3"` 当三级标题，整篇正文会被标成 70 个 `###`，**伪造出文档并不存在的结构**。
"""

import pathlib
import zipfile

import pytest

import extract_docx_text as ed


def _docx(path, body_xml: str):
    """造一份最小可用的 .docx（只有 document.xml，本脚本也只需要它）。"""
    path = pathlib.Path(path)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
                   'package/2006/content-types"/>')
        z.writestr("word/document.xml",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<w:document xmlns:w="http://schemas.openxmlformats.org/'
                   'wordprocessingml/2006/main"><w:body>'
                   + body_xml +
                   '</w:body></w:document>')
    return str(path)


def _p(text, style=None, outline=None, instr=None):
    ppr = ""
    if style is not None or outline is not None:
        ppr = "<w:pPr>"
        if style:
            ppr += f'<w:pStyle w:val="{style}"/>'
        if outline is not None:
            ppr += f'<w:outlineLvl w:val="{outline}"/>'
        ppr += "</w:pPr>"
    runs = ""
    if instr:
        runs += ('<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                 f'<w:r><w:instrText>{instr}</w:instrText></w:r>'
                 '<w:r><w:fldChar w:fldCharType="separate"/></w:r>')
    runs += f'<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'
    if instr:
        runs += '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
    return f"<w:p>{ppr}{runs}</w:p>"


def _fld_open(instr):
    """开一个跨段落的域（只有 begin + instrText + separate，不闭合）。"""
    return ('<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
            f'<w:r><w:instrText>{instr}</w:instrText></w:r>'
            '<w:r><w:fldChar w:fldCharType="separate"/></w:r>'
            '<w:r><w:t>目 录</w:t></w:r></w:p>')


def _fld_close(text=""):
    """闭合域；可带一段属于正文的文字。"""
    return ('<w:p><w:r><w:fldChar w:fldCharType="end"/></w:r>'
            + (f'<w:r><w:t>{text}</w:t></w:r>' if text else "") + "</w:p>")


def test_instr_text_field_codes_are_not_body_text(tmp_path):
    """域指令（TOC/PAGEREF/HYPERLINK）绝不能当正文留下。

    用**真实的跨段目录域**结构：`begin + instrText + separate` 在第一段，
    目录条目在后续段，`end` 收在最后——这正是 Word 的写法，
    也是"只跳 instrText 不够"的原因（域结果同样要剥，见下一个测试）。
    """
    body = (_fld_open('TOC \\o "1-3" \\h \\z \\u')
            + _p("一、绪论 ……………………………… PAGEREF _Toc235530059 \\h - 2 -")
            + _fld_close("正文开始"))
    path = _docx(tmp_path / "a.docx", body)
    lines, _, _ = ed.extract(path)
    joined = "\n".join(lines)
    assert "TOC" not in joined and "PAGEREF" not in joined and "HYPERLINK" not in joined
    assert "正文开始" in joined


def test_xml_entities_are_decoded(tmp_path):
    """`&quot;`／`&gt;` 必须还原成 `"`／`>`，不得原样漏出。"""
    path = _docx(tmp_path / "a.docx", _p("他说&quot;可以&quot; &gt; 不可以"))
    lines, _, _ = ed.extract(path)
    body = "\n".join(lines)
    assert "&quot;" not in body and "&gt;" not in body
    assert '他说"可以" > 不可以' in body


def test_toc_field_result_is_stripped_by_default(tmp_path):
    """目录域的**渲染结果**默认剥掉；`keep_toc=True` 时保留。

    只跳 instrText 仍会把整份目录当正文（实测多出 17 行 `一、…\\t- 2 -`），
    而其中的 `- 2 -` 正是 §5.1 警告过的不可靠页码。
    """
    body = (_fld_open('TOC \\o "1-3"')
            + _p("一、甲 ………………………………… - 2 -")
            + _p("二、乙 ………………………………… - 3 -")
            + _fld_close("正文开始"))
    path = _docx(tmp_path / "a.docx", body)

    lines, _, _ = ed.extract(path, keep_toc=False)
    joined = "\n".join(lines)
    assert "- 2 -" not in joined and "- 3 -" not in joined, "目录域结果未被剥掉"
    assert "目 录" not in joined
    assert "正文开始" in joined

    lines2, _, _ = ed.extract(path, keep_toc=True)
    joined2 = "\n".join(lines2)
    assert "- 2 -" in joined2 and "正文开始" in joined2, "keep_toc=True 时应保留"


def test_bare_digit_style_is_not_a_heading(tmp_path):
    """★回归：裸数字样式（`"3"`）是中文模板的**正文变体**，不得当标题级别。

    实测某政策文件 70 段全是 `pStyle="3"` 且无 outlineLvl；
    旧实现把 `"3"` 当三级标题 → 整篇正文变成 70 个 `###`，伪造文档结构。
    """
    body = "".join(_p(f"这是正文第 {i} 段，不是标题。", style="3") for i in range(5))
    path = _docx(tmp_path / "a.docx", body)
    lines, n, _ = ed.extract(path)
    assert n == 5
    assert not any(x.startswith("#") for x in lines), "裸数字样式被误判成标题"


def test_outline_level_and_explicit_heading_style_do_work(tmp_path):
    """大纲级别是权威来源；显式标题样式名也同样认。"""
    body = (_p("一级标题", outline=0)
            + _p("正文。")
            + _p("二级标题", style="Heading2"))
    path = _docx(tmp_path / "a.docx", body)
    lines, _, _ = ed.extract(path)
    assert "# 一级标题" in lines
    assert "## 二级标题" in lines
    assert "正文。" in lines


def test_heading_from_numbering_is_opt_in(tmp_path):
    """中文序号启发式**默认关闭**，显式开启才生效。"""
    body = _p("一、指导思想") + _p("（一）坚持立德树人。") + _p("正文。")
    path = _docx(tmp_path / "a.docx", body)

    off, _, _ = ed.extract(path, heading_from_numbering=False)
    assert not any(x.startswith("#") for x in off)

    on, _, _ = ed.extract(path, heading_from_numbering=True)
    assert "## 一、指导思想" in on
    assert "### （一）坚持立德树人。" in on


def test_para_numbers_option(tmp_path):
    path = _docx(tmp_path / "a.docx", _p("甲") + _p("乙"))
    lines, _, _ = ed.extract(path, with_para_numbers=True)
    assert lines == ["[¶1] 甲", "[¶2] 乙"]


def test_non_docx_raises(tmp_path):
    bad = tmp_path / "not.docx"
    bad.write_text("这不是 docx", encoding="utf-8")
    with pytest.raises(ValueError):
        ed.extract(str(bad))
    assert ed.main([str(bad)]) == 1


def test_missing_file_returns_1(tmp_path):
    assert ed.main([str(tmp_path / "没有这个.docx")]) == 1
