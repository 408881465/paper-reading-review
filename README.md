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
现有文献综述 → 现有文献批评 → 空白 → 理论依据 → 作者提出的主要问题
→ 研究结果 → 一致/相反的研究发现 → 作者给出的答案 + 未来研究建议。
**断链处即论文的病征**，也是 review 的抓手。

三种产出形态：单篇（**详版 + 一页速览**）、多篇同主题综述、多篇对比评述。
每篇均含「值得关注的引用文献」一节。

## 安装

将本目录放入 `~/.workbuddy/skills/paper-reading-review/`，重启 WorkBuddy 即可。
若手动安装，请确保目录名与 `SKILL.md` 的 `name:` 字段一致（全小写）。

**依赖**：Python 3（仅 `extract_pdf_text.py` 需要 `pymupdf`）：

```bash
pip install pymupdf
```

## 使用

直接对 WorkBuddy 说：

- 「帮我读这篇论文」／「这篇论文讲了什么」
- 「生成论文导读」／「这篇值得引用吗」
- 「把这几篇文献做成综述」
- 「帮我找出这些论文的研究空白」

脚本亦可单独使用：

```bash
# PDF → 带页码标记的纯文本
python3 scripts/extract_pdf_text.py <pdf> -o /tmp/paper.txt

# RCOS 整合表校验 + 主题聚类提示
python3 scripts/build_review.py init rcos.csv
python3 scripts/build_review.py rcos rcos.csv
```

`extract_pdf_text.py` 的退出码：`0` 正常、`1` 打不开或缺依赖、`2` 多数页无文本层
（扫描件，需先 OCR）。**扫码件本脚本不代做 OCR**，只会报警——空文本不等于
「原文没写」，别据空文件写 review。

`build_review.py rcos` 的退出码：`0` 无阻断问题、`1` 有阻断问题（如空表、缺必需栏）。
聚类提示只是词频参考，可用 `--stopwords` / `--keep` 增删词表。

## 目录结构

```
paper-reading-review/
├── SKILL.md
├── references/
│   ├── reading-codes.md      # 密码定义、位置、推导链、章节速查
│   ├── review-workflow.md    # 三形态骨架、详版+速览、自检清单
│   └── cited-literature.md   # 引用文献两组的判据与格式
├── assets/
│   ├── single-review-template.md     # 单篇详版模板
│   ├── quick-review-template.md      # 单篇速览模板（一页纸）
│   ├── multi-review-template.md      # 多篇综述模板
│   ├── comparative-review-template.md# 对比评述模板
│   └── rcos-template.csv             # RCOS 整合表模板
└── scripts/
    ├── extract_pdf_text.py   # PDF → 带页码标记纯文本
    └── build_review.py       # RCOS 校验 + 主题聚类
```

## 已知约束

- 技能名必须**全小写**（官方校验器拒绝大写开头，如 `Paper-`）。
- 单篇详版正文字数区间 **1500–10000 字**；超限时在文末标注字数并给出
  压缩取舍顺序，不要逐句微调（单轮仅压 50–150 字）。
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