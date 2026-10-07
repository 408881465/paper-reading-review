"""batch_extract 的行为测试。

核心断言：抽到了才有文本路径；**没文本层的绝不能留下空文件冒充已抽**；
非 PDF 不被静默跳过而是标成待其它提取器。
"""

import csv
import os

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


def test_non_pdf_marked_for_other_extractor(project):
    reg, outdir, src = project
    (src / "指南.docx").write_bytes(b"PK\x03\x04fake")
    sc.main(["scan", reg, "--source", str(src)])

    be.main(["--registry", reg, "--outdir", outdir])
    rows = _read(reg)
    assert rows[0]["解码状态"] == "待其它提取器"
    assert "非 PDF" in rows[0]["备注"]


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
