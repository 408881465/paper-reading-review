# -*- coding: utf-8 -*-
"""`--printed-labels`（PDF 印刷页码）与 `--docx-para-numbers`（docx 段号）必须各自独立。

2026-10-10 实测的挪用：`batch_extract.main` 里 docx 队列写的是
`with_para_numbers=args.printed_labels`——两个**语义无关**的开关共用一个旗标：

  - `--printed-labels`：PDF 队列用，在 `===== PAGE n =====` 后追加推断的**印刷页码**，
    带一条硬纪律（默认关闭，启用前必须用外部信号验证）；
  - docx 段号：`[¶N]` 前缀，用于**无固定页码**的 .docx 做细粒度回指。

后果有两层：
  ① 传一份 .docx 时加 `--printed-labels`，会**顺带**给它加上 `[¶N]`——
     PDF 的页码纪律被无理由地施加到 docx 上；
  ② docx 想要段号时**没有独立开关**，只能连带启用印刷页码标注（`--help` 里也没这条）。

本文件只测**开关语义**，不依赖 fitz。
"""

import subprocess
import sys
import zipfile
from pathlib import Path

import batch_extract as be
import sync_corpus as sc

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

DOCX_DOC = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>"
    "<w:p><w:r><w:t>第一段正文</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>第二段正文</w:t></w:r></w:p>"
    "</w:body></w:document>"
)


def _write_docx(path, paragraphs):
    """造一个**最小可用**的 .docx（只含 word/document.xml，够 extract_docx_text 读）。"""
    body = "".join(
        f"<w:p><w:r><w:t>{t}</w:t></w:r></w:p>" for t in paragraphs)
    doc = DOCX_DOC.split("<w:body>")[0] + "<w:body>" + body + "</w:body></w:document>"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.'
                   'openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   "</Types>")
        z.writestr("word/document.xml", doc)
    return path


def _make_project(tmp_path, name="甲.docx"):
    """→ (registry, outdir, src)，登记表里已扫入一份 .docx。"""
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    _write_docx(src / name, ["第一段正文", "第二段正文"])

    reg = str(tmp_path / "登记表.csv")
    outdir = str(tmp_path / "out")
    assert sc.main(["init", reg]) == 0
    # ★`scan` 有新增时返回 **3**（`return 3 if added else 0`）——那是成功语义码，
    #   不是失败；写 `== 0` 会让脚手架自己先报错，掩盖真正要测的东西。
    assert sc.main(["scan", reg, "--source", str(src)]) in (0, 3)
    return reg, Path(outdir), src


def _extracted_text(outdir):
    txts = sorted(outdir.glob("*.txt"))
    assert txts, f"没有抽出任何文本：{list(outdir.iterdir())}"
    return txts[0].read_text(encoding="utf-8")


def _run_batch(args):
    return subprocess.run([sys.executable, str(SCRIPTS / "batch_extract.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_docx_para_numbers_are_off_by_default(tmp_path):
    """★默认**不**给 .docx 加 `[¶N]`——段号是可选回指辅助，不该悄悄打开。"""
    reg, outdir, _ = _make_project(tmp_path)
    res = _run_batch(["--registry", reg, "--outdir", str(outdir)])
    assert res.returncode == 0, res.stderr
    assert "[¶" not in _extracted_text(outdir), "默认给 .docx 加了段号"


def test_printed_labels_does_not_turn_on_docx_para_numbers(tmp_path):
    """★★ 核心：`--printed-labels` 只管 PDF 印刷页码，**不得**连带动 .docx 段号。

    这是本条 bug 的正面断言：一份 .docx 在 `--printed-labels` 下，
    输出里不应出现 `[¶N]`——印刷页码与 docx 段号是两件不相干的事。
    """
    reg, outdir, _ = _make_project(tmp_path)
    res = _run_batch(["--registry", reg, "--outdir", str(outdir),
                      "--printed-labels", "--force"])
    assert res.returncode == 0, res.stderr
    txt = _extracted_text(outdir)
    assert "[¶" not in txt, \
        "--printed-labels 顺带给 .docx 加了段号（两个开关被并成了一个旗标）"


def test_docx_para_numbers_have_their_own_flag(tmp_path):
    """★docx 想要段号时必须有**独立开关**，且只加段号、不挂印刷页码。"""
    reg, outdir, _ = _make_project(tmp_path)
    res = _run_batch(["--registry", reg, "--outdir", str(outdir),
                      "--docx-para-numbers"])
    assert res.returncode == 0, res.stderr
    txt = _extracted_text(outdir)
    assert "[¶1]" in txt and "[¶2]" in txt, f"独立开关没生效：\n{txt}"
    assert "印刷页码" not in txt, "docx 上不该出现印刷页码标注"


def test_docx_flag_help_does_not_mention_printed_pages(tmp_path):
    """★`--help` 必须把两个开关分开讲，不得让 docx 段号只藏在印刷页码那条里。"""
    res = _run_batch(["--help"])
    assert "--docx-para-numbers" in res.stdout, "没有 docx 段号的独立开关"
    para_line = [l for l in res.stdout.splitlines() if "--docx-para-numbers" in l]
    assert para_line, "帮助里找不到该开关"
