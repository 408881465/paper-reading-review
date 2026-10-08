"""batch_extract 的行为测试。

核心断言：抽到了才有文本路径；**没文本层的绝不能留下空文件冒充已抽**；
非 PDF 不被静默跳过而是标成待其它提取器。
"""

import csv
import os
import pathlib

import pytest

import batch_extract as be
import sync_corpus as sc

fitz = pytest.importorskip("fitz")


def _make_pdf(path, pages_text):
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page()
        if text:
            page.insert_text((72, 100), text, fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


def _make_blank_pdf(path, n=2):
    """没有任何文本层的 PDF——模拟扫描件。"""
    doc = fitz.open()
    for _ in range(n):
        doc.new_page()
    doc.save(str(path))
    doc.close()
    return str(path)


def _read(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture
def project(tmp_path):
    reg = str(tmp_path / "reg.csv")
    outdir = str(tmp_path / "02_文本")
    src = tmp_path / "src"
    src.mkdir()
    sc.main(["init", reg])
    return reg, outdir, src


def test_extracts_text_pdf_and_updates_registry(project):
    reg, outdir, src = project
    _make_pdf(src / "有文本层_张三.pdf", ["Hello world " * 40, "Second page " * 40])
    sc.main(["scan", reg, "--source", str(src)])

    assert be.main(["--registry", reg, "--outdir", outdir]) == 0
    rows = _read(reg)
    assert rows[0]["解码状态"] == "已抽文本"
    txt = rows[0]["文本路径"]
    assert os.path.exists(txt)
    content = open(txt, encoding="utf-8").read()
    assert "===== PAGE 1 =====" in content and "===== PAGE 2 =====" in content
    assert int(rows[0]["页数"]) == 2 and int(rows[0]["字符数"]) > 100


def test_scanned_pdf_is_marked_for_ocr_and_leaves_no_file(project):
    reg, outdir, src = project
    _make_blank_pdf(src / "扫描件_李四.pdf", n=2)
    sc.main(["scan", reg, "--source", str(src)])

    assert be.main(["--registry", reg, "--outdir", outdir]) == 2   # 有 OCR 队列
    rows = _read(reg)
    assert rows[0]["解码状态"] == "需OCR"
    assert rows[0]["文本路径"] == ""
    # 关键：不能留下空壳 txt
    assert not [f for f in os.listdir(outdir) if f.endswith(".txt")]


def test_second_run_skips_already_extracted(project, capsys):
    reg, outdir, src = project
    _make_pdf(src / "有文本层_张三.pdf", ["A " * 300])
    sc.main(["scan", reg, "--source", str(src)])
    be.main(["--registry", reg, "--outdir", outdir])
    capsys.readouterr()

    assert be.main(["--registry", reg, "--outdir", outdir]) == 0
    assert "待抽文本 0 篇" in capsys.readouterr().out


def test_unsupported_format_marked_for_other_extractor(project):
    """★2026-10-08 变更：`.docx` 已有抽取器，不再是"待其它提取器"。

    旧测试用假 docx 字节（`PK\x03\x04fake`）走这条路；现在它会被 docx
    分支接走。这里改用**确实没有抽取器**的格式（.pptx）守住这条支线。
    """
    reg, outdir, src = project
    (src / "课件.pptx").write_bytes(b"PK\x03\x04fake")
    sc.main(["scan", reg, "--source", str(src)])

    be.main(["--registry", reg, "--outdir", outdir])
    rows = _read(reg)
    assert rows[0]["解码状态"] == "待其它提取器"
    assert "暂无抽取器" in rows[0]["备注"]


def test_broken_docx_is_marked_unopenable_not_silently_skipped(project):
    """坏掉的 .docx 要标「打不开」并留下原因，不能静默略过。"""
    reg, outdir, src = project
    (src / "坏的.docx").write_bytes(b"PK\x03\x04fake")
    sc.main(["scan", reg, "--source", str(src)])

    be.main(["--registry", reg, "--outdir", outdir])
    row = _read(reg)[0]
    assert row["解码状态"] == "打不开", row["解码状态"]
    assert "docx" in row["备注"]
    assert row["文本路径"] == ""


def test_missing_source_is_flagged(project):
    reg, outdir, src = project
    path = _make_pdf(src / "临时_王五.pdf", ["x " * 300])
    sc.main(["scan", reg, "--source", str(src)])
    os.remove(path)

    be.main(["--registry", reg, "--outdir", outdir])
    assert _read(reg)[0]["解码状态"] == "源文件缺失"


def test_safe_filename_strips_path_hostile_chars():
    assert be.safe_filename("关于“AI+工程”/课程：建设?") == "关于_AI+工程_课程_建设"
    assert be.safe_filename("") == "untitled"
    assert len(be.safe_filename("长" * 200)) <= 60


def test_report_is_written(project):
    reg, outdir, src = project
    _make_pdf(src / "有文本层_张三.pdf", ["A " * 300])
    sc.main(["scan", reg, "--source", str(src)])
    be.main(["--registry", reg, "--outdir", outdir])
    assert os.path.exists(os.path.join(outdir, "_提取报告.md"))


def _make_pdf_with_meta(path, text, title):
    """同一文本层、不同元数据 → 文件字节不同（模拟"重新保存过的同一篇"）。"""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), text, fontsize=12)
    doc.set_metadata({"title": title, "author": title})
    doc.save(str(path))
    doc.close()
    return str(path)


def test_text_level_duplicates_are_reported(project, capsys):
    """回归：文件 sha1 不同但抽取文本一致，必须被检出并只在备注里给建议。"""
    reg, outdir, src = project
    _make_pdf_with_meta(src / "同一篇_张三.pdf", "CONTENT " * 60, "版本A")
    _make_pdf_with_meta(src / "同一篇_张三_副本.pdf", "CONTENT " * 60, "版本B")
    sc.main(["scan", reg, "--source", str(src)])

    be.main(["--registry", reg, "--outdir", outdir])
    out = capsys.readouterr().out
    assert "版本重复" in out

    rows = _read(reg)
    assert len(rows) == 2                     # 两份都抽了文本
    dup = [r for r in rows if "文本层一致" in r["备注"]]
    assert len(dup) == 1
    # 硬约束：脚本不得替人做取舍
    assert dup[0]["档位"] == "待分流"
    assert dup[0]["解码状态"] == "已抽文本"


def test_no_dup_check_flag_disables_report(project, capsys):
    reg, outdir, src = project
    _make_pdf_with_meta(src / "同一篇_张三.pdf", "CONTENT " * 60, "版本A")
    _make_pdf_with_meta(src / "同一篇_张三_副本.pdf", "CONTENT " * 60, "版本B")
    sc.main(["scan", reg, "--source", str(src)])

    be.main(["--registry", reg, "--outdir", outdir, "--no-dup-check"])
    assert "版本重复" not in capsys.readouterr().out
    assert not any("文本层一致" in r["备注"] for r in _read(reg))


def _make_pdf_with_printed_pages(path, first_printed: int, n: int = 4):
    """每页页首写印刷页码，正文另有内容——模拟 CNKI 期刊 PDF 的常见版式。"""
    doc = fitz.open()
    for i in range(n):
        page = doc.new_page()
        page.insert_text((72, 50), str(first_printed + i), fontsize=10)
        page.insert_text((72, 120), "BODY " * 40, fontsize=11)
    doc.save(str(path))
    doc.close()
    return str(path)


def test_detect_printed_offset():
    doc_raw = ["14\n正文甲", "15\n正文乙", "16\n正文丙", "17\n正文丁"]
    assert be.detect_printed_offset(doc_raw) == 13
    # 推不出恒定偏移（页码形式杂乱）时返回 None，不得瞎猜
    assert be.detect_printed_offset(["头\n正文", "x\n正文", "y\n正文"]) is None


def test_extracted_text_labels_printed_page(project):
    """回归：提取标记必须同时给出印刷页码，否则引用 p.14 无法被机械核对。"""
    reg, outdir, src = project
    _make_pdf_with_printed_pages(src / "有印刷页码_张三.pdf", first_printed=13)
    sc.main(["scan", reg, "--source", str(src)])
    # 默认**不**标注印刷页码（CNKI 两位页码会被抽成两个单数字记号，标错比不标更坏）；
    # 只有显式 --printed-labels 才标注，且必须先经外部信号验证。
    be.main(["--registry", reg, "--outdir", outdir])
    txt = open(_read(reg)[0]["文本路径"], encoding="utf-8").read()
    assert "[印刷页码" not in txt, "默认不得标注印刷页码"
    be.main(["--registry", reg, "--outdir", outdir, "--printed-labels", "--force"])
    txt = open(_read(reg)[0]["文本路径"], encoding="utf-8").read()
    assert "===== PAGE 1 ===== [印刷页码 p.13]" in txt
    assert "===== PAGE 4 ===== [印刷页码 p.16]" in txt


def test_printed_offset_survives_running_head():
    """回归：CNKI 页首常有栏目眉，印刷页码不在第一行——只看首行会整体漏检。"""
    import re as _re
    pages = ["FOCUS\n本期策划\n14\n正文甲", "FOCUS\n本期策划\n15\n正文乙",
             "FOCUS\n本期策划\n16\n正文丙"]
    assert be.detect_printed_offset(pages) == 13
    # 页码只在页尾也要能检出
    tail = ["正文甲\n14", "正文乙\n15", "正文丙\n16"]
    assert be.detect_printed_offset(tail) == 13


def test_no_impossible_printed_labels(project):
    """回归：依偏移外推不得标出 p.0 / p.-7 这类不可能的页码。"""
    reg, outdir, src = project
    # 前 3 页无页码（封面/摘要），后 3 页印刷页码为 1/2/3 → 偏移为 -3（前段会推出负数）
    doc = fitz.open()
    for i in range(6):
        page = doc.new_page()
        page.insert_text((72, 120), "BODY " * 40, fontsize=11)
        if i >= 3:
            page.insert_text((72, 50), str(i - 2), fontsize=10)
    path = src / "外推_李四.pdf"
    doc.save(str(path)); doc.close()

    sc.main(["scan", reg, "--source", str(src)])
    be.main(["--registry", reg, "--outdir", outdir, "--no-dup-check", "--printed-labels"])
    txt = open(_read(reg)[0]["文本路径"], encoding="utf-8").read()
    assert "p.-" not in txt and "p.0]" not in txt
    assert "[印刷页码 p.1]" in txt


def test_force_does_not_clobber_ocr_rows(project, tmp_path):
    """回归：--force 重抽不得把已 OCR 的图片型文献打回「需OCR」并清空文本路径。"""
    reg, outdir, src = project
    _make_blank_pdf(src / "扫描件_李四.pdf", n=2)          # 无文本层 → 需OCR
    sc.main(["scan", reg, "--source", str(src)])
    be.main(["--registry", reg, "--outdir", outdir, "--no-dup-check"])
    assert _read(reg)[0]["解码状态"] == "需OCR"

    # 模拟已完成 OCR：人工把 OCR 文本挂上
    ocr = tmp_path / "ocr.txt"
    ocr.write_text("===== PAGE 1 =====\nOCR 后的正文\n", encoding="utf-8")
    fields, rows = sc.load_registry(reg)
    rows[0]["文本路径"], rows[0]["解码状态"] = str(ocr), "已OCR"
    sc.save_registry(reg, fields, rows)

    be.main(["--registry", reg, "--outdir", outdir, "--force", "--no-dup-check"])
    row = _read(reg)[0]
    assert row["解码状态"] == "已OCR" and row["文本路径"] == str(ocr)


# ---------------------------------------------------------------- 去重行不得被抽取
# 2026-10-08 用真实 PDF 复现的缺陷：batch_extract 只按"有没有文本"挑行，
# 于是照抽 sync_corpus 已判为重复的行（档位=去重-重复/纳入判定=去重/状态=跳过），
# 把「跳过」覆盖成「已抽文本」，产出两份 sha1 完全相同的文本——
# 正好制造了它自己在报告里警告的"同一篇文献被读两遍"。

def test_is_dedup_row_recognises_all_three_markers():
    assert be.is_dedup_row({"档位": "去重-重复"})
    assert be.is_dedup_row({"纳入判定": "去重"})
    assert be.is_dedup_row({"解码状态": "跳过"})
    assert not be.is_dedup_row({"档位": "T2重要", "纳入判定": "纳入（背景）",
                               "解码状态": "未处理"})
    assert not be.is_dedup_row({})


def test_dedup_row_is_not_extracted_and_keeps_skip_status(project, capsys):
    """重复行不该被抽，其「跳过」状态不得被覆盖，且跳过要明说、不得静默。"""
    reg, outdir, src = project
    a = src / "文献A.pdf"
    _make_pdf(a, ["内容 " * 60, "第二页 " * 60])
    (src / "文献A 副本.pdf").write_bytes(a.read_bytes())     # 字节完全相同
    sc.main(["scan", reg, "--source", str(src), "--record-duplicates"])

    dup = [r for r in _read(reg) if r["档位"] == "去重-重复"]
    assert dup, "未登记出重复行"
    dup_id = dup[0]["编号"]
    assert dup[0]["解码状态"] == "跳过"

    assert be.main(["--registry", reg, "--outdir", outdir]) == 0

    after = {r["编号"]: r for r in _read(reg)}
    assert after[dup_id]["解码状态"] == "跳过", "去重行的「跳过」被覆盖了"
    txts = list(pathlib.Path(outdir).glob("*.txt"))
    assert len(txts) == 1, f"重复行也被抽了，产出 {len(txts)} 份文本"
    out = capsys.readouterr().out
    assert "重复" in out, "跳过重复行时必须明确提示，不得静默"


def test_redo_ocr_alone_actually_re_extracts(project, tmp_path):
    """回归：--redo-ocr 单独使用必须生效。

    缺陷（2026-10-08 用真实登记表复现）：旧实现里「已OCR 保护」那条放行之后，
    又被下一条「有文本且未给 --force 就跳过」拦住，于是**只给 --redo-ocr 完全无效**，
    必须 --redo-ocr --force 同用——而 help 明说该参数"允许重新抽文本"。
    """
    reg, outdir, src = project
    _make_blank_pdf(src / "扫描件_王五.pdf", n=2)
    sc.main(["scan", reg, "--source", str(src)])
    be.main(["--registry", reg, "--outdir", outdir, "--no-dup-check"])

    ocr = tmp_path / "ocr.txt"
    ocr.write_text("===== PAGE 1 =====\n已 OCR 的正文\n", encoding="utf-8")
    fields, rows = sc.load_registry(reg)
    rows[0]["文本路径"], rows[0]["解码状态"] = str(ocr), "已OCR"
    sc.save_registry(reg, fields, rows)

    # 默认：保护，不动
    be.main(["--registry", reg, "--outdir", outdir, "--no-dup-check"])
    assert _read(reg)[0]["解码状态"] == "已OCR"

    # 只给 --redo-ocr：必须放行并重抽（该篇无文本层 → 回到 需OCR，文本路径清空）
    be.main(["--registry", reg, "--outdir", outdir, "--redo-ocr", "--no-dup-check"])
    row = _read(reg)[0]
    assert row["解码状态"] == "需OCR", "--redo-ocr 单独使用未生效"


def test_docx_goes_through_docx_extractor_not_pending(project):
    """★.docx 必须被真正抽取，而不是标成「待其它提取器」。

    此前非 PDF 一律标「待其它提取器」，而本课题 4 份政策文件全是 .docx
    （教育部通知、通识指南、上海素养框架、地方课程监测报告），
    政策文件恰是申报书要引的关键依据——链路实际是断的。
    产出**不含 PAGE 标记**（docx 无固定页码，回指基准是章节名）。
    """
    import zipfile
    reg, outdir, src = project
    docx = src / "政策_关于加强XX的通知.docx"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml",
                   '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
                   'wordprocessingml/2006/main"><w:body>'
                   '<w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr>'
                   '<w:r><w:t>一、指导思想</w:t></w:r></w:p>'
                   '<w:p><w:r><w:t>正文内容若干，足够长以通过检查。</w:t></w:r></w:p>'
                   '</w:body></w:document>')
    sc.main(["scan", reg, "--source", str(src)])
    assert be.main(["--registry", reg, "--outdir", outdir]) == 0

    row = _read(reg)[0]
    assert row["解码状态"] == "已抽文本", f"docx 未被抽取：{row['解码状态']}"
    assert row["文本路径"] and os.path.exists(row["文本路径"])
    body = open(row["文本路径"], encoding="utf-8").read()
    assert "===== PAGE" not in body, "docx 不该产出 PAGE 标记（无固定页码）"
    assert "# 一、指导思想" in body, "章节标题应保留（章节名回指用）"
    assert row["页数"] == "", ".docx 的页数栏应留空，不能写 0"


def test_extract_message_is_not_appended_twice(project):
    """★同一条消息只写一次。

    重跑（--force／--redo-ocr／多次 scan）会把同一条
    "N/N 页无文本层，需先 OCR" 反复追加，备注栏被自己的重复句填满
    （实测跑 3 次后同一句出现 3 次，status 输出被噪音淹没）。
    """
    reg, outdir, src = project
    _make_blank_pdf(src / "扫描件_赵六.pdf", n=2)
    sc.main(["scan", reg, "--source", str(src)])
    for _ in range(3):
        be.main(["--registry", reg, "--outdir", outdir, "--no-dup-check"])

    note = _read(reg)[0]["备注"]
    assert note.count("无文本层") == 1, f"备注被重复追加：{note}"


def test_decorated_page_numbers_yield_the_right_offset():
    """★页脚带破折号的中文期刊也要能推出偏移（此前整类漏检）。

    构造 6 页、每页页脚为 `— (页序+145)` 的真实形态，应推出 145。
    """
    doc_raw = [f"正文内容\n—\n— {i + 146}" for i in range(6)]
    assert be.detect_printed_offset(doc_raw) == 145


def test_detect_printed_offset_shares_criterion_with_extractor():
    """两处判据必须是**同一份**：此前各写一份正则，修一处漏一处。

    实测教训：中文期刊页脚 `— 146` 在两边都不认，于是整篇不做印刷页码标注。
    """
    ex = be._load_extractor()
    # ★必须给足页数：采信门槛是 hits ≥ max(2, 半数页)，单页永远返回 None
    #   （这是合理设计——一页无法证明"偏移恒定"）。
    decorated = [f"正文\n— {i + 146}" for i in range(4)]
    assert be.detect_printed_offset(decorated, ex) == 145

    western = [f"正文\nPage {i + 1} of 17" for i in range(4)]
    assert be.detect_printed_offset(western, ex) is None, "西文页脚不该被当作印刷页码"

    noisy = [f"正文\n2020.12" for _ in range(4)]
    assert be.detect_printed_offset(noisy, ex) is None
