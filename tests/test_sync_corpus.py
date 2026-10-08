"""sync_corpus 的判重与增量语义测试。

重点覆盖三条容易退化的性质：
1. 重跑扫描不产生重复行（幂等）；
2. 人工判断字段（档位/纳入判定/主题分类…）不被脚本覆盖；
3. 文件名归一后同名必须报警并指向文本层比对——不得断言「内容不同」，
   因为重复下载/重新保存的同一篇文件字节不同而内容逐字相同。
"""

import csv
import hashlib
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
    """★2026-10-08 变更：内容标记改为**优先于扩展名**。

    旧断言是 `xx指南.docx → 文档`——它把"扩展名优先"的局限固化了下来，
    于是本课题 4 份政策 .docx（文件名里就写着"通知""指南"）全被判为「文档」，
    而来源类型是四档分流的输入。现与 PDF 的处理方式对齐（先看标记，再看格式）。
    无标记的 .docx 仍落回「文档」，这条底线由下一个测试守住。
    """
    assert sc.guess_source_type("xx指南.docx") == "政策文件"   # 变更点
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


# ---------------------------------------------------------------- 来源类型与 OCR 回流
# （2026-10-08 用本课题真实语料查出）

def test_source_type_content_markers_win_over_extension():
    """★内容标记优先于扩展名。

    旧实现把 `.docx → 文档` 放在最前，于是"通知""指南""学位论文"这些标记
    根本没机会生效——实测本课题 4 份政策 .docx 全被判为「文档」，
    而文件名里明明写着「通知」「指南」。来源类型是四档分流的输入，判错影响分档。
    """
    assert sc.guess_source_type("04_L0111_教育部办公厅2024_加强中小学人工智能教育的通知.docx") == "政策文件"
    assert sc.guess_source_type("05_L0042_中小学人工智能通识教育指南2025年版.docx") == "政策文件"
    # 无政策标记的 .docx 仍应是「文档」，不能一律升格成政策文件
    assert sc.guess_source_type("会议记录.docx") == "文档"
    # PDF 的既有行为不变
    assert sc.guess_source_type("某论文.pdf") == "期刊论文"
    assert sc.guess_source_type("某某学位论文.pdf") == "学位论文"


def _reg_with_one(tmp_path, note="", status="需OCR"):
    reg = str(tmp_path / "reg.csv")
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    _write(src / "扫描件_张三.pdf", "假装是 PDF 的字节")
    sc.main(["init", reg])
    sc.main(["scan", reg, "--source", str(src)])      # init 建的是空表，靠 scan 产生行
    fields, rows = sc.load_registry(reg)
    rows[0]["编号"] = "L0001"
    rows[0]["文件名"] = "扫描件_张三.pdf"
    rows[0]["解码状态"] = status
    rows[0]["档位"] = "T2重要"
    rows[0]["纳入判定"] = "纳入（背景）"
    rows[0]["备注"] = note
    sc.save_registry(reg, fields, rows)
    return reg


def test_attach_completes_the_ocr_loop(tmp_path):
    """★`attach` 是 OCR 回流的正式入口——状态机写着"先 OCR 再回主流程"，
    但此前没有任何工具做这件事，抽好的文本只能手工改表。"""
    reg = _reg_with_one(tmp_path)
    text = tmp_path / "ocr.txt"
    text.write_text("===== PAGE 1 =====\nOCR 出来的正文，够长以通过空文本检查。" * 3, encoding="utf-8")

    assert sc.main(["attach", reg, "L0001", "--text", str(text)]) == 0
    row = _read(reg)[0]
    assert row["解码状态"] == "已OCR"
    assert row["文本路径"] == str(text.resolve()) or os.path.exists(row["文本路径"])
    assert int(row["字符数"]) > 10
    # 人的判断字段绝不能被脚本碰
    assert row["档位"] == "T2重要" and row["纳入判定"] == "纳入（背景）"


def test_attach_refuses_missing_and_empty_text(tmp_path):
    """空文本会被下游误读成「原文没写」，必须拒绝挂载。"""
    reg = _reg_with_one(tmp_path)
    assert sc.main(["attach", reg, "L0001", "--text", str(tmp_path / "没有.txt")]) == 1
    empty = tmp_path / "空.txt"
    empty.write_text("   \n", encoding="utf-8")
    assert sc.main(["attach", reg, "L0001", "--text", str(empty)]) == 1
    assert _read(reg)[0]["解码状态"] == "需OCR", "拒绝挂载后状态不应改变"


def test_attach_unknown_id_fails(tmp_path):
    reg = _reg_with_one(tmp_path)
    text = tmp_path / "t.txt"
    text.write_text("正文" * 20, encoding="utf-8")
    assert sc.main(["attach", reg, "L9999", "--text", str(text)]) == 1


def test_status_flags_triaged_but_not_decoded(tmp_path, capsys):
    """★静默停滞检查：已定档 T1/T2 却停在「已抽文本」，必须报待办。

    旧写法只查 `status in ("", "未处理")`——抽出文本后状态变成「已抽文本」，
    这条检查就不再触发，于是"定档了却一直没出解码卡"可以毫无提示地停在原地。
    实测：15 篇 T1/T2 全停在「已抽文本」，而 status 报「待办／异常 0 条」。
    """
    reg = str(tmp_path / "reg.csv")
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "甲.pdf")
    sc.main(["init", reg])
    sc.main(["scan", reg, "--source", str(src)])
    fields, rows = sc.load_registry(reg)
    rows[0]["档位"] = "T1核心"
    rows[0]["解码状态"] = "已抽文本"          # 已抽完，但还没解码
    sc.save_registry(reg, fields, rows)

    sc.main(["status", reg])
    out = capsys.readouterr().out
    assert "尚未解码" in out, "已定档却停在「已抽文本」的没有报出来（静默停滞）"

    # 真正解码之后就不该再报
    rows[0]["解码状态"] = "已解码"
    rows[0]["产出文件"] = "某解码卡.md"
    sc.save_registry(reg, fields, rows)
    sc.main(["status", reg])
    assert "尚未解码" not in capsys.readouterr().out


# ---------------------------------------------------------------- 并发写入（多人并行派活）
# batch-workflow §6 规定"按主题切分、各人写各自目录"，于是**必然多人同时写同一张登记表**，
# 而脚本是「整表读入 → 内存改 → 整表写回」，窗口在 batch_extract 里长达数分钟。
# 2026-10-08 用真实文件确定性复现两例：
#   ① 甲跑 batch_extract 时乙 attach 挂 OCR 文本 → 乙的状态与备注被清空；
#   ② 甲乙各 scan 自己的目录 → 甲的登记全部消失（5/10），双方退出码都是"成功"。

def _row(rid, name, **kw):
    row = {f: "" for f in sc.REGISTRY_FIELDS}
    # sha1 按**文件名**派生：若按编号派生，"甲1.pdf"与"乙1.pdf"会拿到同一 sha1，
    # 于是被当成同一份文件而走去重分支——那不是本组测试要测的丢更新。
    row.update({"编号": rid, "文件名": name, "sha1": hashlib.sha1(name.encode()).hexdigest()})
    row.update(kw)
    return row


def test_merge_renumbers_colliding_new_rows_without_dropping_others():
    """★双方各自新增却分到同一编号 → 我们那行让位重编号，**他人的行必须补回来**。

    这里踩过一次：用一个"读进来的原编号"集合去判断"哪些行已经处理过"，
    重编号后原编号并未被占用，却仍被当成已处理，导致他人的行被整批丢掉。
    """
    ours = [_row("L0001", "甲1.pdf"), _row("L0002", "甲2.pdf")]
    theirs = [_row("L0001", "乙1.pdf"), _row("L0002", "乙2.pdf")]
    merged, conflicts = sc.merge_registry_rows([], ours, theirs)
    assert {r["文件名"] for r in merged} == {"乙1.pdf", "乙2.pdf", "甲1.pdf", "甲2.pdf"}, \
        [r["文件名"] for r in merged]
    assert len({r["编号"] for r in merged}) == 4, "重编号撞车"
    assert len(conflicts) == 2


def test_merge_keeps_theirs_when_both_changed_same_cell():
    """同一格双方都改且不同 → 保留磁盘（他人后写的），并报冲突。"""
    base = [_row("L0001", "x.pdf")]
    ours = [_row("L0001", "x.pdf", 解码状态="已抽文本")]
    theirs = [_row("L0001", "x.pdf", 解码状态="已OCR", 备注="乙挂的")]
    merged, conflicts = sc.merge_registry_rows(base, ours, theirs)
    assert merged[0]["解码状态"] == "已OCR"
    assert merged[0]["备注"] == "乙挂的"
    assert conflicts and conflicts[0][1] == ["解码状态"]


def test_merge_applies_our_change_to_untouched_cells_only():
    """我们改了的格应用、没改的格保留他人的——两边互不覆盖。"""
    base = [_row("L0001", "x.pdf", 解码状态="未处理", 主题分类="旧")]
    ours = [_row("L0001", "x.pdf", 解码状态="已抽文本", 主题分类="旧")]
    theirs = [_row("L0001", "x.pdf", 解码状态="未处理", 主题分类="新")]
    merged, conflicts = sc.merge_registry_rows(base, ours, theirs)
    assert merged[0]["解码状态"] == "已抽文本", "我们改过的格应生效"
    assert merged[0]["主题分类"] == "新", "我们没碰的格应保留他人的"
    assert not conflicts


def test_merge_keeps_their_new_rows_and_ours():
    """他人新增的行、我们新增的行，都要在结果里。"""
    base = [_row("L0001", "旧.pdf")]
    ours = [_row("L0001", "旧.pdf"), _row("L0002", "我们的新.pdf")]
    theirs = [_row("L0001", "旧.pdf"), _row("L0003", "他人的新.pdf")]
    merged, _ = sc.merge_registry_rows(base, ours, theirs)
    assert {r["文件名"] for r in merged} == {"旧.pdf", "我们的新.pdf", "他人的新.pdf"}


def _write_registry_raw(path, fields, rows):
    """**绕过 load/save** 直接写盘——用来模拟"另一个进程"的写入。

    必须绕开 sc.save_registry，否则会刷新本进程记录的文件指纹，
    检测逻辑就不会触发（测试也就测不到东西）。
    """
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({f: r.get(f, "") for f in fields})


def test_save_registry_detects_and_merges_external_change(tmp_path):
    """★主路径：加载后文件被**别的进程**改过，保存时必须先合并再写。

    这一条锁的是"检测外部改动"本身。只测 merge_registry_rows（单元级）
    或传 allow_external_change=True，都会绕过检测——变异测试证实过：
    把检测关掉，那两条仍然全绿。
    """
    reg = str(tmp_path / "reg.csv")
    sc.main(["init", reg])
    fields, rows = sc.load_registry(reg)
    rows.append(_row("L0001", "甲.pdf", 解码状态="已抽文本", 主题分类="甲的判断"))
    sc.save_registry(reg, fields, rows)

    # 本进程重新加载（= 记住基线），随后**别人**改了同一行的另一格
    fields, rows = sc.load_registry(reg)
    theirs = [dict(r) for r in rows]
    theirs[0]["解码状态"] = "已OCR"
    theirs[0]["备注"] = "另一个进程挂的"
    _write_registry_raw(reg, fields, theirs)          # ← 绕过 save，模拟外部写入

    # 我们保存时改的是**另一格**：合并后两边的改动都应在
    rows[0]["主题分类"] = "我们的判断"
    sc.save_registry(reg, fields, rows)

    result = _read(reg)[0]
    assert result["解码状态"] == "已OCR", "检测未触发：他人的改动被整表覆盖了"
    assert "另一个进程挂的" in result["备注"]
    assert result["主题分类"] == "我们的判断", "我们的改动没写进去"


def test_save_registry_reports_conflict_on_same_cell(tmp_path, capsys):
    """同一格双方都改 → 保留磁盘版（他人），并在 stderr 报冲突。"""
    reg = str(tmp_path / "reg.csv")
    sc.main(["init", reg])
    fields, rows = sc.load_registry(reg)
    rows.append(_row("L0001", "甲.pdf", 解码状态="未处理"))
    sc.save_registry(reg, fields, rows)

    fields, rows = sc.load_registry(reg)
    theirs = [dict(r) for r in rows]
    theirs[0]["解码状态"] = "已OCR"
    _write_registry_raw(reg, fields, theirs)

    rows[0]["解码状态"] = "已抽文本"                   # 同一格，双方都改
    sc.save_registry(reg, fields, rows)

    assert _read(reg)[0]["解码状态"] == "已OCR", "同格冲突应保留他人的"
    assert "并发" in capsys.readouterr().err


def test_save_registry_merges_rows_added_by_others(tmp_path):
    """别人新增的行不能在保存时丢掉。"""
    reg = str(tmp_path / "reg.csv")
    sc.main(["init", reg])
    fields, rows = sc.load_registry(reg)
    rows.append(_row("L0001", "甲.pdf"))
    sc.save_registry(reg, fields, rows)

    fields, rows = sc.load_registry(reg)
    theirs = [dict(r) for r in rows] + [_row("L0009", "他人新增.pdf")]
    _write_registry_raw(reg, fields, theirs)

    rows.append(_row("L0010", "我们新增.pdf"))
    sc.save_registry(reg, fields, rows)

    names = {r["文件名"] for r in _read(reg)}
    assert names == {"甲.pdf", "他人新增.pdf", "我们新增.pdf"}, names


def test_scan_twice_in_sequence_has_no_false_conflict(tmp_path):
    """幂等性不能被并发检查破坏：顺序执行的两次 scan 不该报冲突。"""
    reg = str(tmp_path / "reg.csv")
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "甲.pdf")
    sc.main(["init", reg])
    assert sc.main(["scan", reg, "--source", str(src)]) is not None
    before = _read(reg)
    sc.main(["scan", reg, "--source", str(src)])
    assert _read(reg) == before, "重复扫描不该改动登记表"


def test_force_init_warns_before_destroying_id_bindings(tmp_path, capsys):
    """★覆盖非空登记表会销毁「编号 ↔ 文件」绑定，必须警告。

    编号是**扫描顺序派生**的，而技能纪律说它是**身份字段**；产出文件名、RCOS、
    解码卡里到处嵌着它。实测（2026-10-09）：三个文件登记为 L0001/L0002/L0003，
    加入一个**排序在前**的新文件后重建 → 编号**整体后移一位**，
    于是按旧编号写的产出全部指错文献——而 `init --force` **毫无提示**。
    """
    reg = str(tmp_path / "reg.csv")
    src = tmp_path / "src"
    src.mkdir()
    _write(src / "甲.pdf")
    sc.main(["init", reg])
    sc.main(["scan", reg, "--source", str(src)])

    capsys.readouterr()
    assert sc.main(["init", reg, "--force"]) == 0
    err = capsys.readouterr().err
    assert "编号" in err and "增量" in err, f"覆盖非空表未给出编号绑定警告：{err!r}"

    # 空表（或无表）时不吵
    capsys.readouterr()
    sc.main(["init", reg, "--force"])
    assert "销毁全部" not in capsys.readouterr().err


def test_incremental_scan_keeps_existing_ids_stable(tmp_path):
    """★增量的正确性：新增文件**不得**改变既有行的编号。

    这是 §4 协议的核心性质，也是"不要重建登记表"这条纪律的依据。
    """
    reg = str(tmp_path / "reg.csv")
    src = tmp_path / "src"
    src.mkdir()
    # ★内容必须各不相同：内容相同会被 sha1 判重跳过（技能行为正确），
    #   那样测试就测不到"编号稳定性"了。
    _write(src / "b.pdf", "内容-b")
    _write(src / "d.pdf", "内容-d")
    sc.main(["init", reg])
    sc.main(["scan", reg, "--source", str(src)])
    before = {r["文件名"]: r["编号"] for r in _read(reg)}

    # 加一个**排序在前**的新文件，用**增量** scan
    _write(src / "a.pdf", "内容-a")
    sc.main(["scan", reg, "--source", str(src)])
    after = {r["文件名"]: r["编号"] for r in _read(reg)}

    for name, rid in before.items():
        assert after[name] == rid, f"增量同步改变了既有编号：{name} {rid} → {after[name]}"
    assert "a.pdf" in after and after["a.pdf"] not in before.values(), "新文件未获得新编号"
