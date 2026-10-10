# -*- coding: utf-8 -*-
"""退化 CSV（无表头行）必须给**可读提示**，不得漏出 Python 堆栈。

2026-10-10 实测四种形态：
  - 0 字节文件、只有换行、首行为空行 → `load_rows` 抛
    `ValueError("CSV 无表头行")`，而 `main` 里**没有捕获** → 用户看到 traceback；
  - **首行就是数据行**（最容易被撞到的一种）→ 反而不报错：把数据行当表头，
    于是必需列全判"缺失"、还多报「未识别的列」——一个**误导性**的结论。
    RCOS 表头是「编号/作者/年份/…」，而数据行是「1/张三/2020/…」，
    用**有没有认出必需列**就能把它与"表头写得不规范"区分开。

`build_review.py rcos` 的退出码约定（README）：0 无阻断问题、1 有阻断问题。
"""

import csv
import subprocess
import sys
from pathlib import Path

from conftest import build_review as B

_bm = B

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"

HEADER = ["序号", "作者", "年份", "标题", "来源", "现有文献综述", "作者对现有文献的批评",
          "空白", "研究结果", "作者对未来研究的建议", "批评点/待探讨问题"]
DATA_ROW = [1, "张三", 2020, "标题1", "期刊", "综述内容", "批评内容",
            "空白内容", "结果内容", "建议内容", "批评内容"]


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPTS / "build_review.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def write_raw(path, content):
    """按**原始字节**写文件——退化形态（0 字节／只有换行）要精确控制，不能走 csv.writer。"""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        fh.write(content)
    return path


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        w.writerows(rows)
    return path


# ------------------------------------------------------------ 不得漏出堆栈

def test_zero_byte_csv_gives_readable_error_not_traceback(tmp_path):
    """★0 字节文件：报告要能读懂，且不得出现 `Traceback` / `ValueError` 字样。"""
    path = write_raw(tmp_path / "zero.csv", "")
    res = run("rcos", str(path), "--check-only")
    assert res.returncode == 1, f"退化输入应判为阻断（退出码 1），实际 {res.returncode}"
    assert "Traceback" not in res.stderr, f"漏出了 Python 堆栈：\n{res.stderr}"
    assert "ValueError" not in res.stderr, f"漏出了内部异常名：\n{res.stderr}"
    assert "表头" in (res.stderr + res.stdout), "提示里没说清是「表头」有问题"


def test_blank_first_line_csv_gives_readable_error(tmp_path):
    """★首行为空行：同一族的另一种形态，必须同样给可读提示。"""
    path = write_raw(tmp_path / "blank.csv", "\n作者,年份\n甲,2020\n")
    res = run("rcos", str(path), "--check-only")
    assert res.returncode == 1
    assert "Traceback" not in res.stderr
    assert "ValueError" not in res.stderr


def test_only_newline_csv_gives_readable_error(tmp_path):
    path = write_raw(tmp_path / "nl.csv", "\n")
    res = run("rcos", str(path), "--check-only")
    assert res.returncode == 1
    assert "Traceback" not in res.stderr
    assert "ValueError" not in res.stderr


# ------------------------------------------------------------ 首行是数据行

def test_data_row_as_header_is_reported_as_missing_header(tmp_path):
    """★首行就是数据行（没有表头）→ 必须判成「无表头行」，不是「缺必需栏」。

    实测踩过：这种文件**不抛异常**，而是把「1,张三,2020,…」当成表头——
    于是报一堆「缺少必需列: 作者, 年份, 现有文献综述, 研究结果（作者发现了什么）」
    外加「未识别的列: 1, 张三, 2020, …」。看着像"表头写得不对"，实则**表头整行缺失**，
    用户会去逐列改表头名，而真正该做的是补上表头行。
    """
    path = write_csv(tmp_path / "data_only.csv", [])          # 只写表头
    write_raw(path, ",".join(str(c) for c in DATA_ROW) + "\n")  # 再用数据行覆盖掉表头
    _, _, problems, _ = _bm.check(str(path))
    assert any("表头行" in p for p in problems), \
        f"首行是数据行时没报「未找到表头行」：{problems}"
    joined = " ".join(problems)
    assert "缺少必需列" not in joined, \
        f"把「没表头」误诊成了「表头名不对」，会引导用户去改列名：{problems}"


def test_data_row_as_header_does_not_leak_into_unknown_columns(tmp_path):
    """★同一形态的另一面：数据行**不该**被当成列名报「未识别的列」。

    ⚠️ `mapping` 不一定是空：数据行里若恰好有某个**非必需列**的合法别名
    （本样本第 5 格是「期刊」，而「期刊」正是 `source` 的别名），它会被正常映射。
    判据是**必需列一个都没认出**——那才是"没有表头"；有一两个非必需列撞上别名，
    不影响结论。
    """
    path = write_csv(tmp_path / "data_only.csv", [])
    write_raw(path, ",".join(str(c) for c in DATA_ROW) + "\n")
    _, mapping, _, warnings = _bm.check(str(path))
    assert not [f for f in _bm.REQUIRED if f in mapping], \
        f"数据行被当成了表头并映射出了必需列：{mapping}"
    assert not any("未识别" in w or "被忽略" in w for w in warnings), \
        f"数据行被当成列名报了出来：{warnings}"


# ------------------------------------------------------------ 别把正常表误伤

def test_unusual_but_real_header_is_not_called_missing(tmp_path):
    """★守卫（防收得过紧）：表头用**别名写法**（不规范但认得出）→ 仍是"缺列"，不是"无表头"。

    这条与上面两条是同一条判准的两端：只有**必需列一个都没认出**才算「无表头行」。
    「作者（年）」「主要发现」都是 `FIELD_ALIASES` 里登记过的别名——
    表头写得随意但认得出来，就不该被扣上「无表头行」。
    """
    path = write_raw(tmp_path / "odd_header.csv",
                     "作者（年）,哪一年,别人做过什么,主要发现\n"
                     "张三,2020,综述内容,结果内容\n")
    _, mapping, problems, _ = _bm.check(str(path))
    assert mapping, "守得过紧：必需列**认得出**就不该判成无表头"
    assert "author" in mapping or "rof" in mapping, f"别名写法没被认出来：{mapping}"
    assert not any("无表头" in p or "没有表头" in p for p in problems), \
        f"写法不规范但有真实表头的表被误判成「无表头行」：{problems}"


def test_normal_table_is_unaffected(tmp_path):
    """★守卫：正常表的行为不得被这次改动碰到。"""
    path = write_csv(tmp_path / "ok.csv", [DATA_ROW, DATA_ROW])
    rows, mapping, problems, _ = _bm.check(str(path))
    assert len(rows) == 2 and not problems, f"正常表被误伤：problems={problems}"

def test_single_data_line_with_no_recognizable_column(tmp_path):
    """★`mapping` 恰好为**空**的一行数据，同样要判成「未找到表头行」。

    实测踩过（本批自查）：判据一度写成 `if mapping and not [必需列命中]`——
    短路的 `mapping` 非空前提把这一个形态漏掉了：`甲,2020` 既认不出任何必需列、
    也认不出任何其他列，`mapping == {}`，于是绕过新判据、又回到
    「缺少必需列 + 未识别的列」那条歧路。
    """
    path = write_raw(tmp_path / "one_line.csv", "甲,2020\n")
    _, mapping, problems, _ = _bm.check(str(path))
    assert mapping == {}, f"这一行不该映射出任何列：{mapping}"
    assert any("表头行" in p for p in problems), f"未判成「未找到表头行」：{problems}"
    assert not any("缺少必需列" in p for p in problems), \
        f"又回到「缺必需列」的歧路：{problems}"

