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


def test_image_is_routed_to_need_ocr_not_other_extractor(project, capsys):
    """★图片是 **OCR 候选**，不是"没有抽取器"。

    batch-workflow §3 的状态机写「需OCR（**图片型**，先 OCR 再用 attach 回流）」，
    而「待其它提取器」的定义是"暂无抽取器的格式（如 pptx）"。
    实测（2026-10-08，课题库里 6 个真实 .png）：旧实现把两者混为一谈，
    于是图片全部落在「待其它提取器」——按状态机找「需OCR」行的人**找不到图片**，
    报告里的「需 OCR：N 篇」**永远不含图片**。
    """
    reg, outdir, src = project
    (src / "扫描页.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake" * 20)
    sc.main(["scan", reg, "--source", str(src)])
    be.main(["--registry", reg, "--outdir", outdir])

    row = _read(reg)[0]
    assert row["解码状态"] == "需OCR", f"图片应走需OCR，实际 {row['解码状态']}"
    assert "OCR" in row["备注"]

    # 汇总与报告必须把它算进去（否则数字与登记表不符）
    out = capsys.readouterr().out
    assert "需OCR 1" in out, f"图片型的需OCR未并入汇总：{out[-200:]}"
    assert "需 OCR：1" in open(os.path.join(outdir, "_提取报告.md"),
                              encoding="utf-8").read()


def test_unsupported_format_still_goes_to_other_extractor(project):
    """图片之外的未知格式仍走「待其它提取器」——两条支线不能混。"""
    reg, outdir, src = project
    (src / "课件.pptx").write_bytes(b"PK\x03\x04fake")
    sc.main(["scan", reg, "--source", str(src)])
    be.main(["--registry", reg, "--outdir", outdir])
    assert _read(reg)[0]["解码状态"] == "待其它提取器"


def test_split_digit_page_footer_disables_labeling():
    """★页脚页码被拆成相邻单数字 → 拒绝标注，而不是猜一个。

    实测（2026-10-08，171 篇真实文献）：128 篇有标注的文件里 **10 篇（8%）**带此特征。
    典型实证：某篇 PDF 1 末行 `['…人工智能', '3', '1']` —— 印刷页码 31 被抽成两个记号。
    此时取任一个都会让整篇偏移差 10 的倍数，而**内部一致性查不出这类错**。
    技能的既有立场是"标注错误比不标更坏"，故检出即不标注。
    """
    # 每页页脚都被拆成 '3' '1' 这类相邻单数字
    pages = [f"正文内容若干\n3\n1" for _ in range(4)]
    assert be.detect_printed_offset(pages) is None, "带拆分特征却仍标注了"

    # 对照：正常两位数页脚（未被拆）应能推出偏移
    normal = [f"正文内容若干\n{i + 31}" for i in range(4)]
    assert be.detect_printed_offset(normal) == 30


# ═══════════════════════════════════════════════════════════════════════════════
# 坏件隔离：加密 / 损坏 / 截断 / 零字节 / 伪装扩展名
# 2026-10-09 用 9 个自造的坏 PDF 测出——此前"打不开"分支只测过"文件不存在"。
# ═══════════════════════════════════════════════════════════════════════════════

def _make_good_pdf(path, pages=3):
    import fitz
    d = fitz.open()
    for i in range(pages):
        p = d.new_page()
        p.insert_text((72, 100), f"Page {i+1} body text.",
                      fontname="china-s" if False else "helv")
    d.save(str(path))
    d.close()
    return path


def _extract(path, out):
    return be.extract_one(str(path), str(out), be._load_extractor())


def test_encrypted_pdf_is_reported_unopenable_not_crashing(tmp_path):
    """★用户口令加密的 PDF 必须判「打不开」，**不得抛异常**。

    `fitz.open()` 对加密件**不报错**（打开"成功"），要等到取页才抛
    `ValueError: document closed or encrypted`。实测：这曾让 `batch_extract`
    **整批中断**——后续文件全没处理、登记表没落盘、已抽文本成孤儿。
    `needs_pass` 是独立状态，必须单独判。
    """
    import fitz
    good = _make_good_pdf(tmp_path / "g.pdf")
    enc = tmp_path / "enc.pdf"
    d = fitz.open(str(good))
    d.save(str(enc), encryption=fitz.PDF_ENCRYPT_AES_256,
           user_pw="secret", owner_pw="owner")
    d.close()

    status, pages, chars, msg = _extract(enc, tmp_path / "o.txt")
    assert status == "打不开"
    assert "加密" in msg and "口令" in msg
    assert not (tmp_path / "o.txt").exists(), "打不开的件不应留下空壳文本"


def test_damaged_pdf_without_text_is_not_mistaken_for_scanned(tmp_path):
    """★「结构损坏」与「真·无文本层」都取不到文本，但处置相反——必须分开判。

    实测（2026-10-09）：一份**截断到 40%** 的 PDF 被 MuPDF 容错打开（3 页、0 文本），
    于是被判成「需OCR」→ **用户会去 OCR 一个坏文件而白费功夫**，真正原因是文件损坏。
    判据用 `is_repaired`：真图片型 False／损坏 True。

    ★构造必须让文件**能打开、页数正常、但零文本**，才走得到该判据：
    截断件常直接 `FileDataError`（走另一分支，测不到这里）。
    所以用「**空白页 PDF + 坏 magic**」——实测 is_repaired=True 且文本 0。
    """
    import fitz
    blank = tmp_path / "b.pdf"
    d = fitz.open()
    for _ in range(2):
        d.new_page()
    d.save(str(blank))
    d.close()
    damaged = tmp_path / "damaged.pdf"
    raw = blank.read_bytes()
    damaged.write_bytes(b"XXXX" + raw[4:])

    # 前置确认：这份样本确实走 is_repaired 分支（否则测试会静默退化成空断言）
    d = fitz.open(str(damaged))
    assert d.is_repaired is True, "样本未触发 is_repaired，测试失去意义"
    assert sum(len(d[i].get_text().strip()) for i in range(d.page_count)) == 0
    d.close()

    status, pages, chars, msg = _extract(damaged, tmp_path / "o.txt")
    assert status == "打不开", f"损坏件被判成 {status}（绝不能是需OCR）"
    assert "损坏" in msg and "OCR" in msg, f"消息应说明原因并劝阻按图片型去 OCR：{msg!r}"

    # ★对照：结构完好的空白件**必须**仍判「需OCR」，别把判据改死
    good_blank = _extract(blank, tmp_path / "o2.txt")
    assert good_blank[0] == "需OCR", "结构完好的无文本层件被误判"


def test_genuinely_scanned_pdf_still_goes_to_ocr(tmp_path):
    """★真·图片型 PDF（无文本层、结构完好）仍须判「需OCR」——别把判据改死。"""
    import fitz
    blank = tmp_path / "blank.pdf"
    d = fitz.open()
    for _ in range(2):
        d.new_page()
    d.save(str(blank))
    d.close()
    status, pages, chars, msg = _extract(blank, tmp_path / "o.txt")
    assert status == "需OCR" and "无文本层" in msg


def test_repaired_but_readable_pdf_is_ok_with_a_warning(tmp_path):
    """★头部损坏但仍能读出文本 → 判 ok，**但必须警告文本可能不完整**。

    实测：`badheader.pdf`（magic 被改坏）判 ok 并抽出 108 字，
    但文件结构已损坏——不警告的话，读者会把残缺文本当成全文。
    """
    good = _make_good_pdf(tmp_path / "g.pdf")
    raw = good.read_bytes()
    bad = tmp_path / "badheader.pdf"
    bad.write_bytes(b"XXXX" + raw[4:])
    status, pages, chars, msg = _extract(bad, tmp_path / "o.txt")
    assert status == "ok" and chars > 0
    assert "损坏" in msg and "不完整" in msg, f"未警告文本可能不完整：{msg!r}"


def test_malformed_file_does_not_abort_the_whole_batch(tmp_path):
    """★★ 最要紧的一条：**一个坏件不得中断整批，且已处理结果必须落盘**。

    实测（修复前）：加密 PDF 抛 ValueError → 后续文件全没处理、
    登记表**完全没保存**（9 行全是「未处理」，而已抽出的 2 份文本成了孤儿）。
    """
    import csv
    src = tmp_path / "src"
    src.mkdir()
    _make_good_pdf(src / "a_good.pdf")
    (src / "b_fake.pdf").write_text("这不是 PDF，只是改了扩展名。", encoding="utf-8")
    _make_good_pdf(src / "c_good.pdf")
    (src / "d_zero.pdf").write_bytes(b"")
    import fitz
    d = fitz.open(str(src / "a_good.pdf"))
    d.save(str(src / "e_enc.pdf"), encryption=fitz.PDF_ENCRYPT_AES_256,
           user_pw="x", owner_pw="y")
    d.close()
    _make_good_pdf(src / "f_good.pdf")

    reg = str(tmp_path / "reg.csv")
    assert sc.main(["init", reg]) == 0
    # ★`scan` 的退出码：**有新增返回 3**，无新增返回 0（见 cmd_scan 末尾）。
    assert sc.main(["scan", reg, "--source", str(src)]) in (0, 3)
    # ★再叠一层：让 `extract_one` 对**其中一个文件**抛出**未预料**的异常
    #   （前面的坏件都能被 extract_one 优雅处理，测不到"隔离"本身）。
    real_extract = be.extract_one

    def boom(path, out, extractor, with_printed_labels=False):
        if "c_good" in os.path.basename(path):
            raise RuntimeError("模拟未预料异常")
        return real_extract(path, out, extractor,
                            with_printed_labels=with_printed_labels)

    be.extract_one = boom
    try:
        rc = be.main(["--registry", reg, "--outdir", str(tmp_path / "out")])
    finally:
        be.extract_one = real_extract
    assert rc == 0, "坏件不应让整批以非零码结束"

    rows = {r["文件名"]: r for r in csv.DictReader(open(reg, encoding="utf-8-sig"))}
    assert len(rows) == 6
    # ★关键：**所有行都被处理过**，不是停在「未处理」
    assert all(r["解码状态"] != "未处理" for r in rows.values()), \
        f"有行停在未处理（说明中断了）：{[(k, v['解码状态']) for k, v in rows.items()]}"
    # 完好的两份抽出来了——★其中 f_good 排在抛异常的那份**之后**，
    #   它被处理到，正说明异常没有中断整批。
    for name in ("a_good.pdf", "f_good.pdf"):
        assert rows[name]["解码状态"] == "已抽文本", f"{name} 未被抽取"
        assert rows[name]["文本路径"], f"{name} 缺文本路径"
    # 抛未预料异常的那份（打桩所致）与三个坏件，都判打不开
    for name in ("b_fake.pdf", "c_good.pdf", "d_zero.pdf", "e_enc.pdf"):
        assert rows[name]["解码状态"] == "打不开", f"{name} 状态应为打不开"
    assert "RuntimeError" in rows["c_good.pdf"]["备注"], "隔离时未把异常类型写进备注"


# ═══════════════════════════════════════════════════════════════════════════════
# 三条队列的「已抽的跳过」必须一致
# 2026-10-09 四轮增量演练查出：.docx 与 .md/.txt 两条队列没有这个判据。
# ═══════════════════════════════════════════════════════════════════════════════

def test_docx_and_text_queues_skip_already_extracted(tmp_path):
    """★重跑不得重抽 .docx / .md / .txt，更**不得覆盖**已抽出的文本。

    实测（修复前）：重跑时报「成功 3」，而那 3 篇（1 个 .docx + 2 个 .md）
    一篇新的都没有——两条队列**没有**「已抽的跳过」判据，每次重跑都重抽一遍。
    ★危害不在浪费：重抽会 `open(out, "w")` **覆盖已抽文本**——
    **若用户手工修正过 OCR 错字，重跑会静默覆盖掉**。
    """
    import csv as _csv
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("# 手记\n\n正文内容。\n", encoding="utf-8")
    (src / "b.txt").write_text("纯文本正文。\n", encoding="utf-8")
    import zipfile
    docx = src / "c.docx"
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml",
                   '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
                   'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>正文</w:t></w:r></w:p>'
                   '</w:body></w:document>')

    reg = str(tmp_path / "reg.csv")
    out = tmp_path / "out"
    assert sc.main(["init", reg]) == 0
    assert sc.main(["scan", reg, "--source", str(src)]) in (0, 3)
    assert be.main(["--registry", reg, "--outdir", str(out)]) == 0

    made = sorted(out.glob("*.txt"))
    assert len(made) == 3, f"首次未抽出三份：{made}"
    # 记下内容与 mtime，模拟"用户手工修正过"
    stamp = {p: (p.stat().st_mtime_ns, p.read_text(encoding="utf-8")) for p in made}
    # ★手工修正其中一份
    fixed = made[0]
    fixed.write_text(fixed.read_text(encoding="utf-8") + "\n（用户手工补的一行）\n", encoding="utf-8")
    edited = fixed.read_text(encoding="utf-8")

    # 重跑：不应重抽、更不应覆盖
    assert be.main(["--registry", reg, "--outdir", str(out)]) == 0
    assert fixed.read_text(encoding="utf-8") == edited, \
        "重跑覆盖了用户手工修正过的文本！"

    rows = {r["文件名"]: r for r in _csv.DictReader(open(reg, encoding="utf-8-sig"))}
    for name in ("a.md", "b.txt", "c.docx"):
        assert rows[name]["解码状态"] == "已抽文本"


def test_rerun_message_reports_actual_workload(tmp_path, capsys):
    """★重跑的提示要报**真实工作量**（过滤后的篇数），不能报过滤前的。

    实测踩过：msg 在队列过滤**之前**构造，于是显示「另有 1 篇 .docx……另有 2 篇
    .md/.txt 直接读入」，而实际一篇都不会处理；跳过的篇数也没出现。
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("# x\n\n正文。\n", encoding="utf-8")
    reg = str(tmp_path / "reg.csv")
    out = tmp_path / "out"
    sc.main(["init", reg])
    sc.main(["scan", reg, "--source", str(src)])
    be.main(["--registry", reg, "--outdir", str(out)])
    capsys.readouterr()
    be.main(["--registry", reg, "--outdir", str(out)])
    out_text = capsys.readouterr().out
    assert "略过" in out_text, f"重跑未报出跳过的篇数：{out_text!r}"
    assert ".md/.txt 直接读入" not in out_text, "重跑仍报过滤前的工作量"


# ═══════════════════════════════════════════════════════════════════════════════
# 去重守卫必须覆盖三条队列（不只是 PDF）
# 2026-10-10 复现：`is_dedup_row` 全仓只有 1 个调用点，且在 PDF 队列内部——
# `.docx` 与 `.md/.txt` 两条队列走 `continue` 绕过了它。于是已被 sync_corpus
# 判为「去重-重复／纳入判定=去重／状态=跳过」的行照样被抽，状态被覆盖成
# 「已抽文本」，并产出两份内容相同的文本——**正好制造了报告里警告的"读两遍"**。
# 与 2026-10-09 那次（这两条队列缺「已抽的跳过」）是同一族、同一条漏网之鱼：
# **每条新加的守卫只加在 PDF 队列上，另两条队列就被落下。**
# ═══════════════════════════════════════════════════════════════════════════════

def _make_min_docx(path, text="正文内容"):
    """最小可用 .docx（只含 word/document.xml），供队列测试用。"""
    import zipfile
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml",
                   '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
                   'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + text +
                   '</w:t></w:r></w:p></w:body></w:document>')
    return path


def test_dedup_docx_row_is_not_extracted(tmp_path):
    """★.docx 队列同样不得抽取去重行（守卫此前只在 PDF 队列）。"""
    src = tmp_path / "src"
    src.mkdir()
    a = _make_min_docx(src / "通知_张三.docx")
    (src / "通知_张三 副本.docx").write_bytes(a.read_bytes())     # 字节完全相同

    reg = str(tmp_path / "reg.csv")
    out = tmp_path / "out"
    assert sc.main(["init", reg]) == 0
    sc.main(["scan", reg, "--source", str(src), "--record-duplicates"])

    dup = [r for r in _read(reg) if r["档位"] == "去重-重复"]
    assert dup, "未登记出重复行，无法验证"
    dup_id = dup[0]["编号"]
    assert dup[0]["解码状态"] == "跳过"

    assert be.main(["--registry", reg, "--outdir", str(out)]) == 0

    after = {r["编号"]: r for r in _read(reg)}
    assert after[dup_id]["解码状态"] == "跳过", \
        ".docx 去重行的「跳过」被覆盖了——守卫没有覆盖 .docx 队列"
    made = sorted(p.name for p in pathlib.Path(out).glob("*.txt"))
    assert len(made) == 1, f".docx 重复行也被抽了，产出 {len(made)} 份文本：{made}"


def test_dedup_md_row_is_not_extracted(tmp_path):
    """★.md/.txt 队列同样不得抽取去重行。"""
    src = tmp_path / "src"
    src.mkdir()
    note = src / "笔记A.md"
    note.write_text("# 手记\n\n正文内容，足够长以便判重。\n", encoding="utf-8")
    (src / "笔记A 副本.md").write_bytes(note.read_bytes())        # 字节完全相同

    reg = str(tmp_path / "reg.csv")
    out = tmp_path / "out"
    assert sc.main(["init", reg]) == 0
    sc.main(["scan", reg, "--source", str(src), "--record-duplicates"])

    dup = [r for r in _read(reg) if r["档位"] == "去重-重复"]
    assert dup, "未登记出重复行，无法验证"
    dup_id = dup[0]["编号"]
    assert dup[0]["解码状态"] == "跳过"

    assert be.main(["--registry", reg, "--outdir", str(out)]) == 0

    after = {r["编号"]: r for r in _read(reg)}
    assert after[dup_id]["解码状态"] == "跳过", \
        ".md 去重行的「跳过」被覆盖了——守卫没有覆盖 .md/.txt 队列"
    made = sorted(p.name for p in pathlib.Path(out).glob("*.txt"))
    assert len(made) == 1, f".md 重复行也被抽了，产出 {len(made)} 份文本：{made}"


def test_dedup_skip_is_reported_in_message(tmp_path, capsys):
    """去重行被跳过时必须报出来（不得静默），且三条队列计数合并。"""
    src = tmp_path / "src"
    src.mkdir()
    a = _make_min_docx(src / "通知_张三.docx")
    (src / "通知_张三 副本.docx").write_bytes(a.read_bytes())

    reg = str(tmp_path / "reg.csv")
    out = tmp_path / "out"
    assert sc.main(["init", reg]) == 0
    sc.main(["scan", reg, "--source", str(src), "--record-duplicates"])
    assert be.main(["--registry", reg, "--outdir", str(out)]) == 0
    out_text = capsys.readouterr().out
    # ★断言必须用 batch_extract 自己的 msg 措辞：「重复」两字在 sync_corpus scan 的输出里
    #   也会出现（同名不同内容警告），用宽词会让本用例**因错误理由通过**。
    assert "已判为重复" in out_text and "去重决定跳过" in out_text, \
        f"跳过 .docx 重复行时必须由 batch_extract 明确报出，不得静默：{out_text!r}"
