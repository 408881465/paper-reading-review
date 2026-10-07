"""sync_corpus 的判重与增量语义测试。

重点覆盖三条容易退化的性质：
1. 重跑扫描不产生重复行（幂等）；
2. 人工判断字段（档位/纳入判定/主题分类…）不被脚本覆盖；
3. 文件名归一后同名必须报警并指向文本层比对——不得断言「内容不同」，
   因为重复下载/重新保存的同一篇文件字节不同而内容逐字相同。
"""

import csv
import os

import pytest

import sync_corpus as sc


def _write(path, text="内容"):
    path = str(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _read(path):
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture
def registry(tmp_path):
    return str(tmp_path / "01_文献总表" / "文献总表.csv")


def test_init_writes_full_header(registry):
    assert sc.main(["init", registry]) == 0
    with open(registry, newline="", encoding="utf-8-sig") as fh:
        header = next(csv.reader(fh))
    assert header == sc.REGISTRY_FIELDS


def test_init_refuses_to_overwrite_without_force(registry):
    sc.main(["init", registry])
    assert sc.main(["init", registry]) == 1
    assert sc.main(["init", registry, "--force"]) == 0


def test_scan_adds_rows_then_is_idempotent(registry, tmp_path):
    src = tmp_path / "src"
    _write(src / "中小学人工智能课程建设_张三.pdf", "第一篇的内容")
    _write(src / "通用技术项目式学习_李四.pdf", "第二篇的内容")
    sc.main(["init", registry])

    assert sc.main(["scan", registry, "--source", str(src)]) == 3
    rows = _read(registry)
    assert len(rows) == 2
    assert {r["第一作者"] for r in rows} == {"张三", "李四"}
    assert all(r["档位"] == "待分流" for r in rows)
    assert all(r["纳入判定"] == "待核查" for r in rows)

    # 再扫一次：无新增，退出码回到 0
    assert sc.main(["scan", registry, "--source", str(src)]) == 0
    assert len(_read(registry)) == 2


def test_scan_preserves_manual_judgements(registry, tmp_path):
    src = tmp_path / "src"
    _write(src / "a_王五.pdf", "第一篇")
    sc.main(["init", registry])
    sc.main(["scan", registry, "--source", str(src)])

    # 模拟读文队友填了判断
    fields, rows = sc.load_registry(registry)
    rows[0].update({"档位": "T1核心", "纳入判定": "纳入（核心）", "主题分类": "主题3-跨学科协同",
                    "相关度": "5", "解码状态": "已解码", "产出文件": "04_单篇导读/a-导读.md"})
    sc.save_registry(registry, fields, rows)

    _write(src / "b_赵六.pdf", "第二篇")             # 新增一篇
    assert sc.main(["scan", registry, "--source", str(src)]) == 3
    rows = {r["文件名"]: r for r in _read(registry)}
    assert rows["a_王五.pdf"]["档位"] == "T1核心"
    assert rows["a_王五.pdf"]["产出文件"] == "04_单篇导读/a-导读.md"
    assert rows["b_赵六.pdf"]["档位"] == "待分流"


def test_content_duplicate_is_detected(registry, tmp_path):
    src = tmp_path / "src"
    _write(src / "同一篇_张三.pdf", "SAME")
    _write(src / "同一篇_张三_副本.pdf", "SAME")
    sc.main(["init", registry])

    assert sc.main(["scan", registry, "--source", str(src)]) == 3
    rows = _read(registry)
    assert len(rows) == 1                    # 重复内容默认不登记
    assert sc.main(["scan", registry, "--source", str(src),
                    "--record-duplicates"]) == 3
    rows = _read(registry)
    assert len(rows) == 2
    dup = [r for r in rows if r["档位"] == "去重-重复"][0]
    assert dup["纳入判定"] == "去重" and dup["解码状态"] == "跳过"


def test_same_title_different_content_is_flagged(registry, tmp_path):
    src = tmp_path / "src"
    _write(src / "同名文献_张三.pdf", "版本一")
    _write(src / "同名文献_张三 (1).pdf", "版本二内容不同")
    sc.main(["init", registry])
    sc.main(["scan", registry, "--source", str(src)])

    rows = _read(registry)
    assert len(rows) == 2
    # 措辞必须只说「文件名归一后同名」并指向文本层比对，不得断言内容不同：
    # 同一篇重复下载会字节不同、内容逐字相同（实测 5 对）
    flagged = [r for r in rows if r["纳入判定"] == "待核查" and "文件名归一后同名" in r["备注"]]
    assert all("同名不同内容" not in r["备注"] for r in rows)
    assert len(flagged) == 1


def test_normalize_title_keeps_doi_like_names_distinct():
    """回归：DOI 型文件名不能被逐段剥数字，否则两个无关文献会报假「同名」。"""
    a = sc.normalize_title("s40561-025-00413-1.pdf")
    b = sc.normalize_title("s42330-026-00481-6.pdf")
    assert a != b
    assert len(a) > 2 and a.startswith("s40561")


def test_normalize_title_strips_dup_markers():
    assert sc.normalize_title("论文标题_张三_副本.pdf") == sc.normalize_title("论文标题_张三.pdf")
    assert sc.normalize_title("论文标题 (1).pdf") == sc.normalize_title("论文标题.pdf")
    assert sc.normalize_title("论文标题（2）.pdf") == sc.normalize_title("论文标题.pdf")
    assert sc.normalize_title("A.pdf") != sc.normalize_title("B.pdf")


def test_parse_author_title():
    assert sc.parse_author_title("中小学人工智能课程建设_张三.pdf") == ("中小学人工智能课程建设", "张三")
    # 没有下划线：不猜作者，标题保留原名
    assert sc.parse_author_title("2025年人工智能通识教育指南.pdf")[1] == ""
    # 作者段过长（标题自带下划线）时保守放弃
    assert sc.parse_author_title("关于_x_y_z_很长的什么.pdf")[1] == ""


def test_guess_source_type_marks_uncertain():
    assert sc.guess_source_type("xx指南.docx") == "文档"
    assert sc.guess_source_type("中小学人工智能通识教育指南（2025年版）.pdf") == "政策文件"
    assert sc.guess_source_type("某大学硕士学位论文_王五.pdf") == "学位论文"
    assert sc.guess_source_type("框架图.png") == "图片"


def test_status_reports_pending(registry, tmp_path, capsys):
    src = tmp_path / "src"
    _write(src / "a_张三.pdf")
    sc.main(["init", registry])
    sc.main(["scan", registry, "--source", str(src)])
    assert sc.main(["status", registry]) == 0
    out = capsys.readouterr().out
    assert "待分流" in out and "待核查" in out


def test_status_rejects_empty_registry(registry):
    sc.main(["init", registry])
    assert sc.main(["status", registry]) == 1
