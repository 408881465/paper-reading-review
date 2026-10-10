# paper-reading-review

> 读论文并生成 review 文档的 WorkBuddy 技能。
> 方法论出自 Philip Chong Ho Shon《会读才会写：导向论文写作的文献阅读技巧》（韩鹃译，重庆大学出版社）。

多数人读论文的方式是**消遣式阅读**——划线、摘抄、写「××发现……」的句子，
最后写出一份**洗衣店接衣单**式的文献罗列。本技能用一套可执行的**阅读密码表**
替代模糊的「批判性阅读」，使每条评述都能回指原文，每个空白都必须配一个理论依据。

## 方法论

**完整方法论见 [`SKILL.md`](SKILL.md)**，本仓库不重复——
重复会导致两处文档不一致（历史上已发生过）。

一句话说明：14 个「阅读密码」构成**推导链**而非并列清单——
现有文献综述 → 作者对现有文献的批评 → 现有文献研究空白 → 理论依据（研究的必要性） → 作者要做什么
→ 研究结果（作者发现了什么） → 与现有文献观点一致的研究发现 / 与现有文献观点相反的研究发现 → 作者实际做了什么 + 作者对未来研究的建议。
**断链处即论文的病征**，也是 review 的抓手。

## 产出形态

**三种写法**（A/B/C，各有模板），外加**一层外壳**（D 不并列，它编排 A/B/C）：

| 输入 | 形态 | 产出 |
|---|---|---|
| 1 篇 | **A 单篇深度导读** | **详版 + 一页速览**两份 |
| 3+ 篇同主题 | **B 多篇主题综述** | 综述稿 |
| 2+ 篇可对比 | **C 多篇对比评述** | 评述稿 |
| 30+ 篇，或要持续增量追加 | **D 批量分层处理**（外壳） | 登记表 + 分层产出 + 主题综述 |

**各形态**均含「值得关注的引用文献」一节（支柱性文献 + 需要对话的文献）。
形态 D 的全流程见 [`references/batch-workflow.md`](references/batch-workflow.md)。

## 安装

将本目录放入 `~/.workbuddy/skills/paper-reading-review/`，重启 WorkBuddy 即可。
读取 `~/.agents/skills/` 的 Agent 运行时（如 DSH）放到该目录下同样可用。
若手动安装，请确保目录名与 `SKILL.md` 的 `name:` 字段一致（全小写）。

**依赖**：Python 3；`extract_pdf_text.py` 需要 `pymupdf`；`extract_docx_text.py` 与其余脚本**仅用标准库**（.docx 按 zip+XML 直接解析）
（`batch_extract.py` 复用它的抽取逻辑，故同样需要）：

```bash
pip install pymupdf
```

## 使用

直接对 Agent 说：

- 「帮我读这篇论文」／「这篇论文讲了什么」
- 「生成论文导读」／「这篇值得引用吗」
- 「把这几篇文献做成综述」
- 「帮我找出这些论文的研究空白」
- 「文献太多，分档读」「后期还会加新文献」（走形态 D）

脚本亦可单独使用：

```bash
# PDF → 带页码标记的纯文本
python3 scripts/extract_pdf_text.py <pdf> -o /tmp/paper.txt

# RCOS 整合表校验 + 主题聚类提示
python3 scripts/build_review.py init rcos.csv
python3 scripts/build_review.py rcos rcos.csv

# 形态 D：判重与增量同步 → 按登记表批量抽文本 → 成稿机械自检
python3 scripts/sync_corpus.py scan 文献登记表.csv --source <目录>
python3 scripts/batch_extract.py --registry 文献登记表.csv --outdir 02_文本/
python3 scripts/lint_review.py <成稿.md> --form B

# ★批量检查时用 find -print0 | xargs -0 传参：
#   真实语料里常有含空格的文名（实测「L0141_Salah-美国K12 STEM工程教育-章节解码卡.md」），
#   无引号的 $FILES 展开会把它拆成两个路径，linter 只能报「无法读取」——
#   看起来像技能出错，其实是 shell 拆词。
find 03_解码卡 -name '*.md' -print0 | xargs -0 python3 scripts/lint_review.py

# ★lint 检查的是 **review 成稿**——不要把整个工作区一把梭进去：
#   工作区里还有非成稿文档（校验报告、你自己的笔记、脚本生成的报告等），
#   它们会被按某个形态的字数区间检查而误报。技能自己生成的 `_提取报告.md`
#   已按「规范文档」豁免；其余非成稿请用 `--form doc` 显式跳过。

# .docx 会被 batch_extract 自动接走；也可单独抽（无页码，回指用章节名）
#   需要细粒度回指时：batch_extract 加 --docx-para-numbers（每段前加 [¶N]），
#   或单独抽时加 --para-numbers。★两者都默认关闭，且与 --printed-labels
#   （PDF 印刷页码）各管各的——docx 段号不靠印刷页码那个开关开启。
python3 scripts/extract_docx_text.py <文件.docx> -o /tmp/policy.txt

# OCR 完的文本回流登记表（否则「需OCR」是条断头的支线）
python3 scripts/sync_corpus.py attach 文献登记表.csv L0007 --text /tmp/ocr.txt
```

`extract_pdf_text.py` 的退出码：`0` 正常、`1` 打不开或缺依赖、`2` 多数页无文本层
（扫描件，需先 OCR）。**扫描件本脚本不代做 OCR**，只会报警——空文本不等于
「原文没写」，别据空文件写 review。

`build_review.py rcos` 的退出码：`0` 无阻断问题、`1` 有阻断问题（如空表、缺必需栏、**找不到表头行**）。
「找不到表头行」指首行未识别出任何必需列——0 字节文件、首行空白、**首行直接就是数据行**都归此类，会给一行可读提示而不是 Python 堆栈。
聚类提示只是词频参考，可用 `--stopwords` / `--keep` 增删词表。

`lint_review.py` 的退出码：`0` 无 error、`1` 有 error（`--strict` 时 warning 也算）。

### 协作层：多人独立解码后的口径一致性比对

**判准清不清楚，只有让两个互不通气的人读同一篇才能测出来。** 分歧不是谁错，
**它是判准文档的缺陷线索**：

```bash
# 甲、乙各自独立产出一份同一篇的解码卡（禁止互相查看）
python3 scripts/compare_cards.py 甲-解码卡.md 乙-解码卡.md
python3 scripts/compare_cards.py *.md --json      # 机器可读
```

**退出码**：`0` = 无硬分歧；`1` = 有硬分歧（档位／密码有无／回指基准）；`2` = 用法或读文件错误。
硬分歧可用于流水线；**批评点措辞不同属软分歧**，只报告不判失败。

★出分歧后的处置：回到 `references/batch-workflow.md` 与 `reading-codes.md`，
把该处判准补成**可操作的反例或优先级**，再重测一次。**不要靠"多沟通"解决。**

★条目重合用二元字组相似度**只作提示**（换措辞说同一件事时分数会偏低），
工具会把最高相似的对与分数列出来供人工判断——**判定分歧以内容为准，不以分数为准**。
规则可用 `--skip <名>` 关掉，`--help` 列全部规则名。

## 目录结构

```
paper-reading-review/
├── SKILL.md
├── references/
│   ├── reading-codes.md      # 10+4 个密码的定义、位置、推导链、章节速查
│   ├── review-workflow.md    # A/B/C 三形态骨架、详版+速览、引用文献节、自检清单
│   ├── batch-workflow.md     # 形态 D：分档判准、登记表、增量同步、实践类论文适配、交付前一致性三查
│   └── cited-literature.md   # 支柱性文献 / 需要对话的文献：判据与格式
├── assets/
│   ├── single-review-template.md     # 形态 A 详版模板
│   ├── quick-review-template.md      # 形态 A 速览模板（一页纸）
│   ├── multi-review-template.md      # 形态 B 模板
│   ├── comparative-review-template.md# 形态 C 模板
│   ├── decode-card-template.md       # 形态 D：T2/专著章节用一页解码卡
│   ├── triage-registry-template.csv  # 形态 D：文献登记表（18 栏）
│   ├── report-template.md            # 形态 D 收口：总报告（精读声明 + 一致性三查）
│   └── rcos-template.csv             # RCOS 整合表模板
├── scripts/
│   ├── extract_pdf_text.py   # PDF → 带页码标记纯文本
│   ├── extract_docx_text.py  # .docx → 带章节标题纯文本（仅标准库；无页码，回指用章节名）
│   ├── sync_corpus.py        # 形态 D：判重、增量同步、登记表状态
│   ├── batch_extract.py      # 形态 D：按登记表批量抽文本（可中断、可重跑）
│   ├── lint_review.py        # 自检：缩写残留、字数、空白配依据、洗衣店接衣单、产物卫生
│   ├── build_review.py       # RCOS 校验 + 主题聚类提示
│   └── compare_cards.py      # 协作层：多人独立解码的口径一致性比对
└── tests/                    # pytest：脚本行为与 SKILL.md 纪律的回归网
```

## 已知约束

- 技能名必须**全小写**（官方校验器拒绝大写开头，如 `Paper-`）。
- 单篇详版正文字数区间 **1500–10000 字**；超限时在文末标注字数并给出
  压缩取舍顺序，不要逐句微调（单轮仅压 50–150 字）。
- ★上一条的**字数不含第八节「引用信息」**——那一节是**原文的**参考文献表
  （GB/T 7714 著录），不是本导读产出，故不计入上限；多篇／比较评述的
  「值得关注的引用文献」是导读产出，**照常计入**。
- 理论建构型／政策类文献（**无数据**）不适用实证研究的批评点路径，
  改查四条：框架来源是否交代、分层判准是否声明、概念是否同名不同义、
  策略与框架的映射是否建立。完整说明见
  `references/review-workflow.md` 第 2.5 节。

## 许可

代码与文档适用 **[MIT](LICENSE)**。方法论著作权归原作者与出版社所有，
不在授权范围内——详见 LICENSE 文件开头的说明。

## 致谢

Philip Chong Ho Shon，《会读才会写：导向论文写作的文献阅读技巧》，
韩鹃译，重庆大学出版社。书中「阅读密码表」为本技能的方法论来源。