---
name: skill2-document-retrieval
description: 从港股IPO招股书PDF中提取表格、识别章节结构、匹配财务数据模式、执行多关键词联合搜索。当需要解析招股书PDF、提取财务报表或按章节导航时使用。
---

# 文档检索器（Document Retrieval）

从港股 IPO 招股书 PDF 中提取结构化数据，提供 **7 大能力**：表格提取与清洗、关键词搜索、多关键词联合搜索、招股书章节识别、财务数据模式匹配、关键财务报表定位、文档概览摘要。

## 设计理念

港股招股书通常长达 400-600 页，包含 18 个以上标准章节。传统的关键词搜索只能"找到"，不知道"在哪一章"。本 skill 在设计上做了三个层次的抽象：

| 层次 | 能力 | 对应方法 | 解决的问题 |
|---|---|---|---|
| **文档级** | 概览、章节识别 | `get_summary()`, `detect_sections()` | 这份 PDF 有什么内容？关键信息在哪几页？ |
| **页面级** | 表格提取、财务章节定位 | `extract_tables()`, `find_financial_sections()` | 第 N 页有什么表格？合并财务报表在哪？ |
| **内容级** | 关键词搜索、模式匹配、多关键词联合 | `search_context()`, `search_financial_patterns()`, `search_multi()` | 文本中出现了多少次"募集资金"？涉及多少金额？ |

## 触发场景

- 用户上传了一份港股招股书 PDF，需要快速了解其结构和关键章节位置
- 需要在招股书中搜索特定关键词（如"募集资金"、"关联交易"、"风险因素"）
- 需要从 PDF 中提取所有金额数据（港元/人民币）、日期、百分比等结构化信息
- 需要同时搜索多个关键词的交集（如"风险因素 AND 法律诉讼"出现在同一页）
- 需要自动定位合并财务报表（资产负债表、利润表、现金流量表）所在的页码
- 需要批量提取 PDF 中的所有表格，可指定页码范围
- 作为 orchestrator 管线中的文档解析第一步

## 快速开始

```bash
# 1. 文档概览 — 页数、章节分布、表格数量（推荐第一步）
python "<skill-dir>/scripts/retriever.py" <招股书路径.pdf> --summary

# 2. 识别招股书标准章节
python "<skill-dir>/scripts/retriever.py" <招股书路径.pdf> --sections

# 3. 提取财务数据模式（金额、日期、百分比）
python "<skill-dir>/scripts/retriever.py" <招股书路径.pdf> --financial

# 4. 自动定位关键财务报表所在页
python "<skill-dir>/scripts/retriever.py" <招股书路径.pdf> --fin-sections

# 5. 单关键词搜索
python "<skill-dir>/scripts/retriever.py" <招股书路径.pdf> -q "募集资金" --json

# 6. 多关键词联合搜索（AND / OR / NEAR）
python "<skill-dir>/scripts/retriever.py" <招股书路径.pdf> --multi "风险因素" "法律诉讼" --mode and

# 7. 提取指定页范围的表格（含清洗）
python "<skill-dir>/scripts/retriever.py" <招股书路径.pdf> --tables-only --pages 120-160 --clean --json

# 8. 组合使用 — 获取完整分析视图
python "<skill-dir>/scripts/retriever.py" <招股书路径.pdf> --summary --sections --financial --fin-sections --json
```

> `<skill-dir>` 指本 `SKILL.md` 所在的目录。

## 功能详解

---

### 1. 文档概览 `get_summary()`

**回答什么问题**：这份招股书有多大？是什么结构？值不值得深入分析？

**输出内容**：

| 字段 | 说明 |
|---|---|
| `page_count` | 总页数 |
| `total_chars` / `avg_chars_per_page` | 文本总量和每页密度 |
| `pages_with_text` / `blank_pages` | 有文本页数和空白页数（图片型PDF空白页多） |
| `table_count` | 全文档表格总数（快速扫描） |
| `sections_found` / `section_list` | 识别到的章节数及其分布 |

**CLI**：`--summary`

**为什么第一步总是跑 summary**：如果 `is_text_searchable` 为 false（总字符 < 100），说明这是一份扫描件 PDF，没有 OCR 文字层，后续所有搜索都会失败。这时需要先做 OCR 处理。

---

### 2. 章节识别 `detect_sections()`

**回答什么问题**：招股书的标准章节（风险因素、财务资料、募集资金用途等）各在第几页开始？

**检测逻辑**：内置 18 个港股招股书标准章节的正则模式，按顺序扫描全文，记录每个章节首次出现的页码和匹配次数。

**识别的章节**：

| 章节 Key | 中文标签 | 典型匹配关键词 |
|---|---|---|
| `definitions` | 释义/定义 | 释义、定義 |
| `risk_factors` | 风险因素 | 风险因素、風險因素 |
| `waivers` | 豁免遵守上市规则 | 豁免 |
| `company_info` | 公司资料 | 公司资料 |
| `industry_overview` | 行业概览 | 行业概览、市场概览 |
| `history_structure` | 历史及公司架构 | 历史、发展、架构 |
| `business` | 业务 | 业务（排除"业务模式"） |
| `financial_info` | 财务资料 | 财务资料、财务信息 |
| `financial_statements` | 合并财务报表 | 合并财务、财务报表 |
| `use_of_proceeds` | 募集资金用途 | 募集资金、所得款项用途、未来计划 |
| `directors` | 董事及管理层 | 董事、高级管理层 |
| `share_capital` | 股本 | 股本、股份 |
| `substantial_shareholders` | 主要股东 | 主要股东 |
| `underwriting` | 包销/承销 | 包销、承销 |
| `connected_transactions` | 关联交易 | 关联交易 |
| `dividend_policy` | 股息政策 | 股息、股利、分红 |
| `legal_proceedings` | 法律诉讼 | 法律诉讼、重大诉讼 |
| `appendix` | 附录 | 附录、附件 |

**CLI**：`--sections`

---

### 3. 财务数据模式匹配 `search_financial_patterns()`

**回答什么问题**：招股书中出现了多少金额、日期、百分比、股票代码等结构化数据？

**匹配的模式类型**：

| 模式 Key | 标签 | 示例匹配 |
|---|---|---|
| `hkd_amount` | 港元金额 | 港币 1,200,000 元 |
| `hkd_amount_simple` | 港元金额(HK$) | HK$ 1,200,000 |
| `rmb_amount` | 人民币金额 | 人民币 5,000 万元 |
| `date_cn` | 中文日期 | 2024年12月31日 |
| `date_iso` | ISO日期 | 2024-12-31 |
| `percentage` | 百分比 | 15.5% |
| `percentage_range` | 百分比区间 | 10.0% 至 25.0% |
| `stock_code` | 股票代码 | 0700.HK |
| `fiscal_year` | 财政年度 | 截至2024年12月31日 |
| `total_assets` | 总资产金额 | 总资产：5,200,000 |
| `revenue` | 营业收入金额 | 收入：3,800,000 |
| `net_profit` | 净利润金额 | 净利润：850,000 |
| `total_equity` | 权益总额 | 权益总额：2,100,000 |

**CLI**：`--financial`

---

### 4. 关键财务报表定位 `find_financial_sections()`

**回答什么问题**：合并资产负债表在哪几页？现金流量表在哪？

**定位的报表**：

| Key | 标签 |
|---|---|
| `balance_sheet` | 资产负债表 / Balance Sheet |
| `income_statement` | 利润表 / 损益表 / Income Statement |
| `cashflow_statement` | 现金流量表 / Cash Flow Statement |
| `equity_statement` | 权益变动表 / Statement of Changes in Equity |
| `notes` | 财务报表附注 / Notes |

每个报表返回匹配的页码列表和最佳页码（首次出现页）。

**为什么重要**：orchestrator 或用户手动分析时，第一步总是找到三大报表。这个方法省去了在 500 页 PDF 中翻找的时间。

**CLI**：`--fin-sections`

---

### 5. 表格提取 `extract_tables()`

**回答什么问题**：PDF 中有哪些表格？内容是什么？

**功能**：

- 提取所有页面中的所有表格
- 支持**页码范围过滤**（`--pages 120-160`）
- 支持**表格清洗**（`--clean`）：自动处理合并单元格残留、去除空行、规整 None 值
- 输出包含 `page`、`table_index`、`rows`、`row_count`、`col_count`

**清洗逻辑**：

| 操作 | 说明 |
|---|---|
| None 替换 | `None` → 空字符串 |
| 合并单元格去重 | 相邻列相同值 → 保留首列、其余置空 |
| 空行过滤 | 全行为空的行自动剔除 |
| 空白 strip | 去除首尾多余空格 |

**CLI**：`--tables-only [--pages N-M] [--clean]`

---

### 6. 关键词搜索 `search_context()`

**回答什么问题**：这个关键词在招股书中出现了多少次？出现在什么上下文里？

**功能**：全文搜索，返回带上下文的片段，匹配处以 `>>>关键词<<<` 高亮。可调节上下文窗口大小（默认前后各 200 字符）。

**CLI**：`-q "关键词" [-w 窗口大小]`

---

### 7. 多关键词联合搜索 `search_multi()`

**回答什么问题**：哪几页同时提到了"风险因素"和"法律诉讼"？"募集资金"和"收购"在哪些段落里相邻出现？

**三种模式**：

| 模式 | 逻辑 | 适用场景 |
|---|---|---|
| `and` | 所有关键词必须出现在同一页 | "同时涉及 A 和 B 的内容" |
| `or` | 任一关键词出现即可（并集） | "A 或 B 的相关内容" |
| `near` | 所有关键词在 300 字符窗口内出现 | "A 和 B 是否在同一段话里被讨论" |

**CLI**：`--multi "关键词1" "关键词2" [--mode and|or|near] [--near-window 300]`

---

## API

### `DocRetriever(file_path: str)`

| 方法 | 返回值 | 说明 |
|---|---|---|
| `load()` | `dict` | 打开 PDF，返回 `page_count`、`total_chars`、`is_text_searchable` |
| `get_summary()` | `dict` | 文档概览：页数、章节分布、表格数量、文本密度 |
| `detect_sections()` | `dict` | 识别 18 个标准招股书章节，每项含 `label`、`page`、`match_count` |
| `search_financial_patterns(pattern_types=None)` | `dict` | 匹配 13 种财务数据模式，返回 `total_occurrences` + `samples` |
| `find_financial_sections()` | `dict` | 定位 5 类关键财务报表的页码 |
| `extract_tables(page_range=None, clean=False)` | `list[dict]` | 表格提取，支持页码范围 + 清洗 |
| `search_context(query, window=200)` | `list[str]` | 单关键词全文搜索，返回上下文片段 |
| `search_multi(keywords, mode="and", near_window=300)` | `dict` | 多关键词联合搜索（and/or/near） |

## CLI 参数一览

| 参数 | 说明 | 默认值 |
|---|---|---|
| `pdf_path` | 招股书 PDF 路径（必填） | — |
| `--summary` | 文档概览 | — |
| `--sections` / `-s` | 章节识别 | — |
| `--financial` / `-f` | 财务数据模式匹配 | — |
| `--fin-sections` | 关键财务报表定位 | — |
| `--tables-only` / `-t` | 仅提取表格 | — |
| `--clean` / `-c` | 表格清洗 | — |
| `--pages` | 表格提取页码范围（如 `40-55`） | 全部 |
| `--query` / `-q` | 单关键词搜索 | — |
| `--multi` / `-m` | 多关键词列表 | — |
| `--mode` | 多关键词模式：`and`/`or`/`near` | `and` |
| `--near-window` | NEAR 模式的字符窗口 | 300 |
| `--window` / `-w` | 关键词搜索上下文窗口 | 200 |
| `--json` / `-j` | JSON 格式输出 | — |

## 输出示例

### get_summary() + detect_sections()

```json
{
  "file_path": "prospectus.pdf",
  "page_count": 485,
  "total_chars": 324000,
  "pages_with_text": 480,
  "blank_pages": 5,
  "avg_chars_per_page": 668.0,
  "table_count": 87,
  "sections_found": 15,
  "section_list": [
    {"section": "definitions", "label": "释义/定义", "page": 5},
    {"section": "risk_factors", "label": "风险因素", "page": 28},
    {"section": "financial_info", "label": "财务资料", "page": 156},
    {"section": "financial_statements", "label": "合并财务报表", "page": 220},
    {"section": "use_of_proceeds", "label": "募集资金用途", "page": 310}
  ]
}
```

### search_financial_patterns()

```json
{
  "hkd_amount": {
    "label": "港元金额",
    "total_occurrences": 245,
    "samples": ["港币 1,200,000 元", "港币 850,000,000 元"]
  },
  "percentage": {
    "label": "百分比",
    "total_occurrences": 178,
    "samples": ["15.5%", "22.3%", "8.0%"]
  },
  "stock_code": {
    "label": "股票代码",
    "total_occurrences": 3,
    "samples": ["0700.HK"]
  }
}
```

### find_financial_sections()

```json
{
  "balance_sheet": {
    "label": "资产负债表 / Balance Sheet",
    "pages": [220, 221, 222],
    "found": true,
    "best_page": 220
  },
  "income_statement": {
    "label": "利润表 / 损益表 / Income Statement",
    "pages": [224],
    "found": true,
    "best_page": 224
  },
  "cashflow_statement": {
    "label": "现金流量表 / Cash Flow Statement",
    "pages": [226, 227],
    "found": true,
    "best_page": 226
  }
}
```

### search_multi() — AND 模式

```json
{
  "mode": "and",
  "keywords": ["风险因素", "法律诉讼"],
  "total_matches": 3,
  "pages": [35, 42, 188]
}
```

### search_multi() — NEAR 模式

```json
{
  "mode": "near",
  "keywords": ["募集资金", "收购"],
  "total_matches": 5,
  "matches": [
    {"page": 312, "snippet": "...本次 >>>募集资金<<< 主要用于收购..."},
    {"page": 318, "snippet": "..."}
  ]
}
```

## 依赖

- `pdfplumber` — PDF 解析和表格提取（核心依赖）

## 资源

- 脚本：[scripts/retriever.py](scripts/retriever.py)（~850 行，含 7 大功能模块 + 18 章节正则 + 13 财务模式正则）
