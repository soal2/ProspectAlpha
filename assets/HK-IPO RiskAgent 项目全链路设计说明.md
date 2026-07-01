# HK-IPO RiskAgent 项目全链路设计说明

> 项目定位：基于港股 IPO 招股书、港股基本信息、上市前市场行情，构建一个“可解释、可追溯、可复核”的 IPO 风险预警系统。系统重点不是精准预测股价，而是识别上市后短期显著下跌/破发风险，并给出风险诱因、原文证据和 Agent 分析日志。

---

## 0. 核心结论


推荐总链路：

```text
数据接入层
→ 文档解析层
→ 证据检索层
→ 风险特征层
→ 多 Agent 协同层
→ 风险预测层
→ 回测评估层
→ 报告生成与投研复核层
→ API / 原型系统交付
```

三个关键原则：

1. **证据先行**：没有招股书原文证据，不生成风险结论。
2. **专家定义风险，技术实现抽取**：风险分类、阈值、权重应由金融/投研/法务专业人员主导，技术团队负责工程化。
3. **上市前数据用于预测，上市后数据用于验证**：上市后 1/5/20/60 日行情只能作为标签和回测数据，不能作为模型输入。

---

## 1. 数据使用边界

### 1.1 赛题提供的数据

| 数据集 | 是否作为系统输入 | 是否用于评测 | 说明 |
|---|---:|---:|---|
| 招股说明书 PDF | 是 | 是，评估风险抽取和证据定位 | 核心数据源，负责基本面、财务、法务、经营风险识别 |
| 港股基本信息 | 是 | 是 | 股票代码、上市日期、发行价、行业、发行规模等，用于数据对齐、特征构建和收益标签计算 |
| 历史行情数据：上市前部分 | 是 | 间接用于回测 | 用于市场情绪、板块流动性、发行窗口分析 |
| 历史行情数据：上市后部分 | 否 | 是 | 用于计算首日/5日/20日/60日收益、破发、显著下跌标签 |

### 1.2 正确的数据时间切分

假设某公司上市日为 `T`。

```text
可作为系统输入：
- 招股书全文
- 公司基本信息
- 发行价、发行规模、所属行业
- T 日之前的大盘、板块、同行、新股市场表现

不可作为系统输入：
- T 日之后的个股行情
- 上市后 1/5/20/60 日收益率
- 上市后是否破发
```

上市后的数据只用于：

```text
预测结果验证
模型回测
高/中/低风险组收益对比
业务参考价值评估
```

---

## 2. 总体系统架构

```text
┌────────────────────────────────────────────┐
│ 0. 专家风险体系与标注层                    │
│ 金融专家定义风险分类、阈值、权重、标注标准 │
└───────────────────┬────────────────────────┘
                    ↓
┌────────────────────────────────────────────┐
│ 1. 数据接入与元数据对齐层                  │
│ PDF / 股票代码 / 上市日期 / 行业 / 行情     │
└───────────────────┬────────────────────────┘
                    ↓
┌────────────────────────────────────────────┐
│ 2. 文档解析层                              │
│ PDF 解析、OCR、表格抽取、章节识别、页码定位 │
└───────────────────┬────────────────────────┘
                    ↓
┌────────────────────────────────────────────┐
│ 3. 证据库与检索层                          │
│ 原文块、表格块、页码、bbox、Hybrid RAG      │
└───────────────────┬────────────────────────┘
                    ↓
┌────────────────────────────────────────────┐
│ 4. 风险特征层                              │
│ 财务/经营/法务/市场风险特征抽取与计算       │
└───────────────────┬────────────────────────┘
                    ↓
┌────────────────────────────────────────────┐
│ 5. 多 Agent 协同层                         │
│ 财务 Agent、法务 Agent、市场 Agent、总控    │
└───────────────────┬────────────────────────┘
                    ↓
┌────────────────────────────────────────────┐
│ 6. 风险预测层                              │
│ 规则评分卡 + ML 模型，输出风险等级和概率    │
└───────────────────┬────────────────────────┘
                    ↓
┌────────────────────────────────────────────┐
│ 7. 回测评估层                              │
│ 首日/5日/20日/60日表现验证，AUC/Lift/TopK  │
└───────────────────┬────────────────────────┘
                    ↓
┌────────────────────────────────────────────┐
│ 8. 报告生成与投研复核层                    │
│ 风险报告、证据截图、Agent 日志、人工复核    │
└───────────────────┬────────────────────────┘
                    ↓
┌────────────────────────────────────────────┐
│ 9. API / 原型系统交付层                    │
│ FastAPI、Web UI、批量分析、报告导出         │
└────────────────────────────────────────────┘
```

---

# 3. 模块 0：专家风险体系与标注层

## 3.1 模块定位

该模块不是技术模块，而是业务规则基础。它决定系统到底要识别哪些风险、什么情况算高风险、各类风险权重如何设置。

大白话：

```text
金融专家负责告诉系统：什么叫风险。
技术团队负责让系统：自动找到这些风险。
```

## 3.2 输入

| 输入 | 来源 | 示例 |
|---|---|---|
| 赛题要求 | 赛题文档 | 对赌/赎回、关联交易、客户集中度、现金流消耗 |
| 金融专家经验 | 投研/风控/投行/法务人员 | 生物科技企业更关注现金 runway 和核心管线 |
| 历史破发案例 | 历史 IPO 样本 | 高风险样本和低风险样本对比 |
| 招股书风险因素 | 招股书原文 | 风险因素章节中的高频风险描述 |

## 3.3 输出

### 风险 taxonomy 示例

```json
{
  "risk_taxonomy": {
    "financial_risk": [
      "unprofitable",
      "negative_operating_cash_flow",
      "short_cash_runway",
      "declining_gross_margin",
      "high_debt_ratio"
    ],
    "business_risk": [
      "customer_concentration",
      "supplier_concentration",
      "single_product_dependency",
      "core_pipeline_uncertainty"
    ],
    "legal_compliance_risk": [
      "redemption_clause",
      "repurchase_obligation",
      "related_party_transaction",
      "litigation",
      "regulatory_penalty"
    ],
    "market_risk": [
      "weak_pre_listing_market",
      "sector_liquidity_decline",
      "recent_ipo_break_rate_high",
      "valuation_premium"
    ]
  }
}
```

### 风险阈值示例

```json
{
  "customer_concentration": {
    "top5_ratio_high": 0.70,
    "top5_ratio_medium": 0.40
  },
  "cash_runway_years": {
    "high_risk_less_than": 1.5,
    "medium_risk_less_than": 3.0
  },
  "recent_ipo_break_rate": {
    "high_risk_greater_than": 0.60,
    "medium_risk_greater_than": 0.40
  }
}
```

## 3.4 具体需要完成的工作

1. 定义一级风险、二级风险、风险描述。
2. 明确每个风险特征从哪里来：招股书、基本信息、行情数据、公告数据。
3. 为每个特征定义阈值和风险等级。
4. 设计人工标注规范。
5. 标注 30–50 家公司作为 gold set，用于评估风险抽取准确率和证据召回率。
6. 定义哪些风险需要人工复核。

## 3.5 推荐做法

先不要贪多。MVP 阶段优先覆盖 10 个高价值风险：

```text
1. 是否未盈利
2. 经营现金流连续为负
3. 现金 runway 较短
4. 客户集中度高
5. 供应商集中度高
6. 关联交易风险
7. 对赌/赎回条款风险
8. 核心管线未商业化风险
9. 上市前市场情绪偏弱
10. 近期 IPO 破发率较高
```

## 3.6 推荐技术栈

该模块主要不依赖复杂技术。

| 类型 | 推荐 |
|---|---|
| 标注表 | Excel / Google Sheets / 飞书多维表格 |
| 标注平台 | Label Studio / 自研简单 Web 标注页 |
| 规则配置 | YAML / JSON |
| 版本管理 | Git |

## 3.7 建议负责人员与专业技能

| 角色 | 是否主责 | 需要能力 |
|---|---:|---|
| 金融投研/风控专家 | 是 | 港股 IPO、财务分析、估值、破发案例理解 |
| 法务/合规人员 | 是，负责法务风险 | 招股书法务条款、关联交易、特殊股东权利 |
| 数据科学/算法人员 | 参与 | 将风险规则转成可建模特征 |
| 后端/数据工程师 | 参与 | 将规则配置化、结构化存储 |

---

# 4. 模块 1：数据接入与元数据对齐层

## 4.1 模块定位

把赛题提供的三类数据统一整理到系统中，并通过股票代码、公司名称、上市日期建立关联。

## 4.2 输入

### 输入 1：招股书 PDF

```text
/prospectus/2025/03750_CATL_zh.pdf
/prospectus/2024/00300_Midea_zh.pdf
```

### 输入 2：港股基本信息表

```csv
stock_code,company_name,listing_date,industry,offer_price,offer_size,market_cap_range
03750.HK,宁德时代,2025-05-20,新能源,263.00,41006000000,large
00300.HK,美的集团,2024-09-17,家电制造,54.80,35666000000,large
```

### 输入 3：历史行情表

```csv
stock_code,date,open,high,low,close,volume,pct_change
03750.HK,2025-05-20,xxx,xxx,xxx,xxx,xxx,xxx
HSI,2025-05-19,xxx,xxx,xxx,xxx,xxx,xxx
```

## 4.3 输出

统一公司主表：

```json
{
  "company_id": "03750.HK",
  "company_name": "宁德时代",
  "listing_date": "2025-05-20",
  "industry": "新能源",
  "offer_price": 263.00,
  "offer_size_hkd": 41006000000,
  "prospectus_files": [
    {
      "language": "zh",
      "file_path": "/prospectus/2025/03750_CATL_zh.pdf",
      "document_type": "final_prospectus"
    }
  ]
}
```

## 4.4 具体需要完成的工作

1. 统一股票代码格式，例如 `03750`、`3750.HK`、`03750.HK`。
2. 统一公司名称中英文别名。
3. 将招股书 PDF 与公司代码绑定。
4. 将上市日期、发行价、行业、发行规模等结构化字段入库。
5. 将历史行情按日期和股票代码入库。
6. 检查缺失值、异常值和重复文件。
7. 明确行情数据中哪些属于上市前输入，哪些属于上市后标签。

## 4.5 推荐做法

建立三张基础表：

```text
companies
- company_id
- stock_code
- company_name_zh
- company_name_en
- listing_date
- industry
- offer_price
- offer_size
- market_cap_range

prospectus_documents
- document_id
- company_id
- file_path
- language
- document_type
- created_at

market_prices
- stock_code
- date
- open
- high
- low
- close
- volume
- pct_change
```

## 4.6 推荐技术栈

| 任务 | 推荐技术 |
|---|---|
| 数据清洗 | Python、Pandas |
| 数据入库 | PostgreSQL |
| 文件存储 | 本地文件系统 / MinIO |
| 数据校验 | Great Expectations / Pandera |
| 批处理 | Python scripts / Airflow / Prefect |

## 4.7 建议负责人员与专业技能

| 角色 | 需要能力 |
|---|---|
| 数据工程师 | 数据清洗、ETL、数据库建模、批处理 |
| 后端工程师 | 文件管理、API、任务调度 |
| 金融数据人员 | 股票代码、上市日期、发行价、行情口径校验 |

---

# 5. 模块 2：文档解析层

## 5.1 模块定位

把几百页 PDF 招股书拆成机器能处理的结构化内容，包括文本、表格、章节、页码和坐标。

## 5.2 输入

```json
{
  "company_id": "03750.HK",
  "document_id": "doc_03750_zh_final",
  "file_path": "/prospectus/2025/03750_CATL_zh.pdf"
}
```

## 5.3 输出

### 文本块输出示例

```json
{
  "block_id": "blk_03750_001283",
  "company_id": "03750.HK",
  "document_id": "doc_03750_zh_final",
  "page": 128,
  "section": "业务",
  "block_type": "text",
  "text": "截至最后实际可行日期，本集团的主要客户包括...",
  "bbox": [72.1, 118.5, 520.4, 360.2],
  "parser": "pymupdf",
  "confidence": 0.95
}
```

### 表格块输出示例

```json
{
  "block_id": "tbl_03750_000312",
  "company_id": "03750.HK",
  "document_id": "doc_03750_zh_final",
  "page": 312,
  "section": "财务资料",
  "block_type": "table",
  "table_markdown": "| 项目 | 2022 | 2023 | 2024 |\n| 收入 | ... | ... | ... |",
  "bbox": [60.0, 210.0, 540.0, 690.0],
  "parser": "pdfplumber",
  "confidence": 0.82
}
```

## 5.4 具体需要完成的工作

1. PDF 文本抽取。
2. PDF 页码保留。
3. 段落切分。
4. 标题识别和章节归属。
5. 表格识别和结构化。
6. OCR 兜底处理扫描页或图片表格。
7. 记录每个文本块/表格块的 `bbox` 坐标，方便后续生成证据截图。
8. 解析质量打分，例如抽取置信度、表格完整度。

## 5.5 应该如何做

### 推荐解析流程

```text
PDF 文件
→ PyMuPDF 提取页面文本和坐标
→ pdfplumber 提取表格
→ Camelot/Tabula 兜底表格抽取
→ PaddleOCR 处理扫描页或图片表格
→ 章节标题识别
→ 段落/表格块入库
```

### 章节识别方法

优先用规则 + LLM 辅助：

```text
规则识别：
- “概要”
- “风险因素”
- “行业概览”
- “业务”
- “财务资料”
- “历史、重组及公司架构”
- “关联交易”
- “主要股东”
- “董事及高级管理层”
- “附录一 会计师报告”

LLM 辅助：
当标题格式混乱时，判断某个文本块属于哪个章节。
```

### 注意事项

1. 不要只按页切分，否则容易把一个风险证据切碎。
2. 表格跨页时要合并。
3. 财务表格要保留单位，例如人民币千元、港币百万元。
4. 中英文招股书字段可能不同，要建立中英字段词典。

## 5.6 推荐技术栈

| 任务 | 推荐技术 |
|---|---|
| 文本和坐标抽取 | PyMuPDF |
| 表格抽取 | pdfplumber、Camelot、Tabula |
| OCR | PaddleOCR |
| 版面理解 | Docling、Unstructured |
| 数据存储 | PostgreSQL、MinIO |
| 异步任务 | Celery / RQ |

## 5.7 建议负责人员与专业技能

| 角色 | 需要能力 |
|---|---|
| NLP/文档解析工程师 | PDF 解析、OCR、版面分析、表格抽取 |
| 数据工程师 | 文档块存储、批处理、数据质量检查 |
| 金融分析人员 | 判断财务表、章节解析是否符合业务阅读习惯 |

---

# 6. 模块 3：证据库与检索层

## 6.1 模块定位

建立一个可以被 Agent 和风险抽取模块调用的证据库。每次风险判断都必须能回到原始招股书页码、段落和截图。

## 6.2 输入

来自文档解析层的文本块和表格块：

```json
{
  "block_id": "blk_03750_001283",
  "text": "公司的前五大客户收入占比较高...",
  "page": 128,
  "section": "业务",
  "bbox": [72.1, 118.5, 520.4, 360.2]
}
```

## 6.3 输出

检索结果示例：

```json
{
  "query": "前五大客户收入占比",
  "company_id": "03750.HK",
  "results": [
    {
      "block_id": "blk_03750_001283",
      "page": 128,
      "section": "业务",
      "text": "截至报告期，公司来自前五大客户的收入占比分别为...",
      "score": 0.91,
      "bbox": [72.1, 118.5, 520.4, 360.2]
    }
  ]
}
```

## 6.4 具体需要完成的工作

1. 文本块向量化。
2. 建立关键词索引。
3. 建立表格块索引。
4. 建立公司级、章节级过滤条件。
5. 实现 Hybrid Search：关键词检索 + 向量检索。
6. 实现风险专用检索模板。
7. 返回页码、段落、bbox、文件路径。

## 6.5 应该如何做

### 为什么不能只用向量检索

金融文档里有很多关键词非常关键：

```text
赎回
回购
特殊权利
关联交易
前五大客户
经营现金流
未盈利
重大诉讼
```

向量检索可能理解语义，但会漏掉精确术语。因此建议：

```text
BM25 关键词检索 + 向量检索 + 章节过滤 + rerank
```

### 风险检索模板示例

```json
{
  "risk_type": "redemption_clause",
  "queries": [
    "赎回权 回购 特殊权利 投资人权利",
    "redemption repurchase special rights preferred rights"
  ],
  "preferred_sections": [
    "历史、重组及公司架构",
    "主要股东",
    "股本",
    "附录"
  ]
}
```

## 6.6 推荐技术栈

| 任务 | 推荐技术 |
|---|---|
| 向量库 | Qdrant / Milvus / Chroma |
| 关键词检索 | Elasticsearch / OpenSearch / BM25 |
| Embedding 模型 | bge-m3 / text-embedding-3-large / Qwen Embedding |
| Rerank | bge-reranker / Cohere Rerank / LLM rerank |
| 原文块存储 | PostgreSQL |

## 6.7 建议负责人员与专业技能

| 角色 | 需要能力 |
|---|---|
| RAG/NLP 工程师 | 向量检索、BM25、rerank、chunk 策略 |
| 后端工程师 | 检索 API、索引服务、权限和性能 |
| 金融人员 | 设计风险检索词表和同义词表 |

---

# 7. 模块 4：风险特征层

## 7.1 模块定位

风险特征层负责把原始文本、表格、基本信息、上市前行情加工成结构化风险信号。

大白话：

```text
文档解析层：把 PDF 拆开。
风险特征层：从拆开的内容里提炼风险指标。
```

## 7.2 输入

```json
{
  "company_id": "03750.HK",
  "document_blocks": ["blk_001", "tbl_002"],
  "company_meta": {
    "listing_date": "2025-05-20",
    "industry": "新能源",
    "offer_price": 263.00,
    "offer_size_hkd": 41006000000
  },
  "pre_listing_market_data": {
    "hsi_return_20d": -0.05,
    "sector_return_20d": -0.08,
    "recent_ipo_break_rate_30d": 0.62
  }
}
```

## 7.3 输出

```json
{
  "company_id": "03750.HK",
  "feature_set": {
    "financial_features": {
      "is_unprofitable": false,
      "operating_cash_flow_negative_years": 0,
      "cash_runway_years": null,
      "gross_margin_trend": "stable"
    },
    "business_features": {
      "top1_customer_ratio": 0.31,
      "top5_customer_ratio": 0.72,
      "customer_concentration_risk": "high",
      "supplier_concentration_risk": "medium"
    },
    "legal_features": {
      "redemption_clause_found": true,
      "related_party_transaction_risk": "medium",
      "major_litigation_found": false
    },
    "market_features": {
      "pre_listing_market_sentiment": "weak",
      "hsi_return_20d": -0.05,
      "sector_return_20d": -0.08,
      "recent_ipo_break_rate_30d": 0.62
    }
  },
  "evidence": [
    {
      "risk_type": "customer_concentration_risk",
      "page": 128,
      "section": "业务",
      "text": "前五大客户收入占比为...",
      "bbox": [72, 118, 520, 360]
    }
  ]
}
```

## 7.4 具体需要完成的工作

### 7.4.1 财务风险特征

| 特征 | 来源 | 做法 |
|---|---|---|
| 是否未盈利 | 利润表 | 抽取净利润/亏损字段 |
| 经营现金流是否为负 | 现金流量表 | 抽取经营活动现金流净额 |
| 现金 runway | 现金及现金等价物 + 经营现金流 | Python 计算 |
| 毛利率趋势 | 利润表 | 多年度毛利率比较 |
| 收入增长趋势 | 利润表 | 多年度收入增长率 |
| 资产负债率 | 资产负债表 | 总负债 / 总资产 |

现金 runway 示例：

```text
现金 runway = 期末现金及现金等价物 / 最近一年经营现金流净流出
```

### 7.4.2 经营风险特征

| 特征 | 来源 | 做法 |
|---|---|---|
| 客户集中度 | 业务章节、客户表 | 抽取前五大客户收入占比 |
| 供应商集中度 | 业务章节、供应商表 | 抽取前五大供应商采购占比 |
| 单一产品依赖 | 业务和收入分部 | 抽取最大产品收入占比 |
| 核心管线进度 | 生物科技招股书 | 抽取临床阶段、商业化状态 |

### 7.4.3 法务合规风险特征

| 特征 | 来源 | 做法 |
|---|---|---|
| 对赌/赎回条款 | 历史、股东协议、股本章节 | 关键词 + LLM 抽取 |
| 关联交易 | 关联交易章节 | 表格金额 + 交易类型 |
| 重大诉讼 | 法务章节 | LLM 抽取诉讼金额、状态 |
| 监管处罚 | 合规章节 | 抽取处罚主体、金额、原因 |

### 7.4.4 市场情绪风险特征

| 特征 | 时间窗口 | 做法 |
|---|---|---|
| 上市前 5/20 日大盘收益 | T-5/T-20 到 T-1 | 恒生指数收益 |
| 上市前行业板块收益 | T-20 到 T-1 | 行业指数或可比公司平均收益 |
| 近期 IPO 破发率 | T 前 30/60 日 | 历史新股首日/5日破发比例 |
| 近期 IPO 平均首日收益 | T 前 30/60 日 | 新股市场热度 |
| 市场波动率 | T 前 20 日 | 指数收益率标准差 |

## 7.5 应该如何做

建议采用：

```text
规则召回 → LLM 结构化抽取 → 程序计算 → 专家阈值判定 → 证据绑定
```

注意：

1. LLM 负责理解文本，不负责最终财务计算。
2. 所有数字计算由 Python 完成。
3. 每个风险特征必须绑定证据来源。
4. 特征缺失时要记录原因，例如“表格解析失败”“招股书未披露”。

## 7.6 推荐技术栈

| 任务 | 推荐技术 |
|---|---|
| 结构化抽取 | LLM + JSON Schema / Pydantic |
| 数值计算 | Python、Pandas、NumPy |
| 规则引擎 | YAML 规则 / durable_rules |
| 特征存储 | PostgreSQL / Parquet |
| 特征管理 | Feast 可选，MVP 可不用 |

## 7.7 建议负责人员与专业技能

| 角色 | 需要能力 |
|---|---|
| 金融风控/投研专家 | 定义特征、阈值、风险等级 |
| NLP/LLM 工程师 | 结构化抽取、Prompt、Schema 约束 |
| 数据科学家 | 特征计算、缺失处理、统计验证 |
| 数据工程师 | 特征表建设、批量处理 |

---

# 8. 模块 5：多 Agent 协同层

## 8.1 模块定位

模拟真实投行尽调团队，让不同 Agent 分别从财务、法务、业务、市场角度分析风险，再由总控 Agent 汇总。

## 8.2 推荐 Agent 划分

| Agent | 职责 | 主要输入 | 主要输出 |
|---|---|---|---|
| 总控决策 Agent | 规划流程、调度工具、汇总结论 | 公司 ID、任务状态 | 综合风险结论 |
| 文档解析 Agent | 调用解析和检索能力 | PDF、文档块 | 章节、证据块 |
| 财务穿透 Agent | 分析财务风险 | 财务特征、财务证据 | 财务风险清单 |
| 法务合规 Agent | 分析条款和合规风险 | 法务证据、关联交易 | 法务风险清单 |
| 业务/行业 Agent | 分析客户、供应商、产品、管线 | 业务特征 | 经营风险清单 |
| 市场情绪 Agent | 分析发行窗口、流动性 | 上市前行情、IPO 市场数据 | 市场风险评分 |
| 验证审计 Agent | 检查结论是否被证据支持 | 风险结论、证据 | 通过/驳回/需复核 |
| 报告 Agent | 生成报告 | 风险结果、证据、日志 | Markdown/HTML/PDF 报告 |

## 8.3 输入

```json
{
  "company_id": "03750.HK",
  "feature_set": {
    "financial_features": {},
    "business_features": {},
    "legal_features": {},
    "market_features": {}
  },
  "evidence_store_ref": "qdrant_collection_hk_ipo",
  "task": "generate_ipo_risk_warning"
}
```

## 8.4 输出

```json
{
  "company_id": "03750.HK",
  "agent_outputs": {
    "FinancialAgent": {
      "risk_level": "medium",
      "risk_items": [
        {
          "risk_type": "gross_margin_volatility",
          "conclusion": "公司毛利率存在波动，需要关注盈利稳定性。",
          "evidence_ids": ["tbl_03750_000312"],
          "confidence": 0.82
        }
      ]
    },
    "LegalAgent": {
      "risk_level": "high",
      "risk_items": [
        {
          "risk_type": "redemption_clause",
          "conclusion": "发现上市前投资人特殊权利安排，需要重点复核。",
          "evidence_ids": ["blk_03750_002188"],
          "confidence": 0.88
        }
      ]
    },
    "MarketAgent": {
      "risk_level": "high",
      "conclusion": "上市前市场情绪偏弱，近期新股破发率较高。"
    }
  },
  "conflicts": [],
  "verified": true
}
```

## 8.5 具体需要完成的工作

1. 定义 Agent 角色、权限、输入和输出格式。
2. 将关键能力封装成 Skill。
3. 使用状态图编排 Agent 执行顺序。
4. 并行执行财务、法务、业务、市场 Agent。
5. 用 Verifier Agent 检查证据支撑。
6. 记录每个 Agent 的调用日志。
7. 出现冲突时触发重新检索、复算、人工复核。

## 8.6 Skill 设计

| Skill | 输入 | 输出 |
|---|---|---|
| `pdf_parse_skill` | PDF 文件 | 文本块、表格块、页码、坐标 |
| `section_retrieve_skill` | 公司 ID + 章节名 | 相关原文片段 |
| `risk_extract_skill` | 文本片段 + 风险类型 | 风险 JSON |
| `table_extract_skill` | PDF 页码/区域 | 结构化表格 |
| `cash_burn_skill` | 现金流表、现金余额 | 现金 runway、现金消耗率 |
| `customer_concentration_skill` | 客户表 | top1/top5 客户占比 |
| `supplier_concentration_skill` | 供应商表 | top1/top5 供应商占比 |
| `redemption_clause_skill` | 股东协议文本 | 是否存在赎回/回购条款 |
| `related_transaction_skill` | 关联交易章节 | 关联交易风险 |
| `peer_valuation_skill` | 行业、发行估值 | 同行估值偏离 |
| `market_window_skill` | 上市日期、行情 | 上市前市场情绪特征 |
| `evidence_verify_skill` | 风险结论、证据 | 是否支持结论 |
| `report_generate_skill` | 风险结果 | 报告 |

## 8.7 冲突处理机制

### 冲突示例

```text
财务 Agent：现金流风险高。
总控 Agent：综合风险中等。
原因：总控未正确考虑现金 runway。
```

### 处理流程

```text
发现冲突
→ 重新检索原始证据
→ 调用对应 Skill 复算
→ Verifier Agent 判断证据强弱
→ 更新风险结论
→ 若仍无法解决，标记“需人工复核”
```

### 证据优先级

| 证据类型 | 优先级 |
|---|---:|
| 原始财务表格数值 | 最高 |
| 招股书明确披露条款 | 高 |
| 程序计算结果 | 高 |
| LLM 总结 | 中 |
| 模糊文本推断 | 低 |

## 8.8 推荐技术栈

| 任务 | 推荐技术 |
|---|---|
| Agent 编排 | LangGraph |
| LLM 接入 | OpenAI / Claude / Qwen / DeepSeek，可配置 |
| Schema 约束 | Pydantic、JSON Schema |
| 工具调用 | LangChain tools / 自定义 Python function |
| 日志追踪 | PostgreSQL JSONB / OpenTelemetry / LangSmith 思路 |
| 后端服务 | FastAPI |

## 8.9 建议负责人员与专业技能

| 角色 | 需要能力 |
|---|---|
| Agent 工程师 | LangGraph、状态机、工具调用、异常处理 |
| LLM 工程师 | Prompt、结构化输出、防幻觉、Verifier 设计 |
| 金融专家 | 定义 Agent 分析逻辑和复核标准 |
| 后端工程师 | API、任务状态、日志持久化 |

---

# 9. 模块 6：风险预测层

## 9.1 模块定位

风险预测层负责把风险特征和 Agent 结论合成为最终预警结果。

大白话：

```text
风险特征层：发现风险信号。
Agent 层：解释风险信号。
风险预测层：判断最终风险等级。
```

## 9.2 输入

```json
{
  "company_id": "03750.HK",
  "feature_set": {
    "cash_runway_years": 0.9,
    "top5_customer_ratio": 0.72,
    "redemption_clause_found": true,
    "related_party_transaction_risk_score": 60,
    "pre_listing_market_sentiment_score": 80,
    "recent_ipo_break_rate_30d": 0.62
  },
  "agent_risk_scores": {
    "financial_risk_score": 75,
    "legal_risk_score": 85,
    "business_risk_score": 70,
    "market_risk_score": 80
  }
}
```

## 9.3 输出

```json
{
  "company_id": "03750.HK",
  "target_window": "5_trading_days",
  "risk_score": 78.5,
  "risk_level": "high",
  "predicted_event": "significant_drop",
  "predicted_probability": 0.71,
  "top_risk_drivers": [
    "现金 runway 较短",
    "存在赎回/回购类特殊权利",
    "客户集中度较高",
    "上市前市场情绪偏弱"
  ],
  "review_required": true
}
```

## 9.4 具体需要完成的工作

1. 定义预测目标：首日破发、5 日显著下跌、20 日下跌、60 日深度破发。
2. 建立规则评分卡。
3. 建立机器学习分类模型作为辅助。
4. 输出综合风险分、风险等级、预测概率。
5. 输出主要风险驱动因素。
6. 标记需要人工复核的样本。

## 9.5 推荐预测目标

优先目标：

```text
上市后 5 个交易日显著下跌风险
```

可选标签定义：

| 标签 | 定义 |
|---|---|
| 首日破发 | 首日收盘价 < 发行价 |
| 5 日破发 | 第 5 个交易日收盘价 < 发行价 |
| 5 日显著下跌 | 第 5 个交易日收益率 <= -10% |
| 20 日显著下跌 | 第 20 个交易日收益率 <= -15% |
| 60 日深度破发 | 第 60 个交易日收益率 <= -20% |

## 9.6 推荐方案

### MVP 方案：专家规则评分卡

```text
综合风险分 =
0.25 × 财务风险分
+ 0.20 × 经营风险分
+ 0.20 × 法务合规风险分
+ 0.15 × 估值发行风险分
+ 0.20 × 市场情绪风险分
```

风险等级：

| 分数 | 风险等级 |
|---:|---|
| 0–40 | 低风险 |
| 40–70 | 中风险 |
| 70–100 | 高风险 |

### 正式方案：规则评分卡 + LightGBM/XGBoost

```text
最终风险分 = 0.6 × 规则评分卡分数 + 0.4 × ML 模型概率分数
```

推荐不要让 LLM 直接作为最终预测器。LLM 适合做理解、抽取、解释；最终风险分更适合由规则和可回测模型产生。

## 9.7 推荐技术栈

| 任务 | 推荐技术 |
|---|---|
| 规则评分卡 | YAML + Python |
| 机器学习模型 | LightGBM / XGBoost / Logistic Regression / CatBoost |
| 特征解释 | SHAP |
| 模型评估 | scikit-learn |
| 实验追踪 | MLflow，可选 |

## 9.8 建议负责人员与专业技能

| 角色 | 需要能力 |
|---|---|
| 数据科学家/量化建模人员 | 分类模型、回测、特征工程、AUC/Precision@K |
| 金融专家 | 风险权重、业务解释、阈值判断 |
| 后端工程师 | 模型服务化、接口封装 |
| LLM 工程师 | 将风险原因转成可解释文本 |

---

# 10. 模块 7：回测评估层

## 10.1 模块定位

验证系统输出的风险预警是否有业务参考价值。

## 10.2 输入

```json
{
  "company_id": "03750.HK",
  "listing_date": "2025-05-20",
  "offer_price": 263.00,
  "predicted_risk_score": 78.5,
  "predicted_risk_level": "high",
  "post_listing_prices": [
    {"date": "2025-05-20", "close": 250.0},
    {"date": "2025-05-27", "close": 238.0}
  ]
}
```

## 10.3 输出

```json
{
  "company_id": "03750.HK",
  "actual_labels": {
    "first_day_return": -0.049,
    "first_day_break": true,
    "return_5d": -0.095,
    "significant_drop_5d": false,
    "return_20d": -0.18,
    "significant_drop_20d": true,
    "return_60d": -0.25,
    "deep_break_60d": true
  },
  "evaluation": {
    "predicted_high_risk": true,
    "hit_5d_significant_drop": false,
    "hit_20d_significant_drop": true
  }
}
```

## 10.4 具体需要完成的工作

1. 计算首日收益率。
2. 计算上市后 5/20/60 个交易日收益率。
3. 计算是否破发。
4. 计算显著下跌标签。
5. 将预测结果与真实标签对比。
6. 计算整体评估指标。
7. 做高/中/低风险分组回测。

## 10.5 推荐评估指标

不要只看 Accuracy。

| 指标 | 说明 |
|---|---|
| AUC | 看风险分排序能力 |
| Precision@TopK | 高风险 Top K 中有多少真实下跌 |
| Recall | 真实下跌样本中有多少被识别 |
| F1 | 综合准确率与召回率 |
| Lift | 高风险组命中率相对平均水平提升多少 |
| 高低风险组收益差 | 高风险组是否明显表现更差 |
| 高风险组破发率 | 业务可解释性强 |

### 分组回测示例

| 风险组 | 5 日破发率 | 5 日显著下跌率 | 5 日平均收益 |
|---|---:|---:|---:|
| 高风险组 | 68% | 42% | -8.2% |
| 中风险组 | 45% | 25% | -2.1% |
| 低风险组 | 23% | 10% | +3.5% |

这种展示比单纯预测准确率更适合本赛题。

## 10.6 推荐技术栈

| 任务 | 推荐技术 |
|---|---|
| 回测计算 | Python、Pandas、NumPy |
| 指标计算 | scikit-learn |
| 图表 | matplotlib / plotly |
| 实验记录 | MLflow / CSV / PostgreSQL |

## 10.7 建议负责人员与专业技能

| 角色 | 需要能力 |
|---|---|
| 量化/数据科学人员 | 回测、金融指标、分类评估 |
| 金融专家 | 判断评估结果是否有业务意义 |
| 数据工程师 | 行情数据清洗、交易日对齐 |

---

# 11. 模块 8：报告生成与投研复核层

## 11.1 模块定位

把系统分析结果变成投研人员可以看懂、可以复核、可以导出的报告。

## 11.2 输入

```json
{
  "company_id": "03750.HK",
  "risk_prediction": {
    "risk_score": 78.5,
    "risk_level": "high",
    "target_window": "5_trading_days",
    "top_risk_drivers": ["现金 runway 较短", "赎回条款", "市场情绪偏弱"]
  },
  "agent_outputs": {},
  "evidence": [
    {
      "risk_type": "redemption_clause",
      "page": 188,
      "text": "投资人享有特殊赎回权...",
      "bbox": [72, 118, 520, 360]
    }
  ],
  "backtest_result": {}
}
```

## 11.3 输出

### 报告结构

```text
《IPO 风险穿透预警报告》

1. 公司基本信息
2. 综合风险等级
3. 上市后 5 日显著下跌风险判断
4. 核心风险诱因 Top 5
5. 财务风险分析
6. 经营风险分析
7. 法务合规风险分析
8. 市场情绪与发行窗口分析
9. Agent 协同分析摘要
10. 证据溯源：页码、段落、截图
11. 上市后表现回测结果
12. 人工复核建议
```

### 单个风险点展示示例

```markdown
## 风险点：现金流消耗压力

- 风险等级：高
- 触发原因：公司经营现金流连续为负，现金 runway 低于 1.5 年。
- 计算过程：现金 runway = 期末现金及现金等价物 / 最近一年经营现金流净流出。
- 原文证据：招股书第 312 页现金流量表。
- Agent 来源：财务穿透 Agent。
- 复核状态：建议人工复核。
```

## 11.4 具体需要完成的工作

1. 生成 Markdown/HTML 报告。
2. 根据证据 bbox 生成 PDF 页面截图。
3. 在截图上高亮证据区域。
4. 报告中展示每个风险结论的证据页码。
5. 展示 Agent 调用日志。
6. 支持人工修改风险等级、添加复核意见。
7. 支持单家公司报告和批量报告导出。

## 11.5 应该如何做

推荐报告生成流程：

```text
风险预测结果
→ 证据排序
→ LLM 生成报告初稿
→ 模板化校验
→ 插入证据截图
→ 生成 HTML
→ 可选导出 PDF
```

注意：

1. LLM 只负责表达，不允许编造新风险。
2. 报告中的每个风险点必须来自结构化风险结果。
3. 报告中每个关键结论必须绑定证据。
4. 缺证据的内容要标注“需人工复核”。

## 11.6 推荐技术栈

| 任务 | 推荐技术 |
|---|---|
| 报告模板 | Jinja2 / Markdown template |
| HTML 生成 | Jinja2 + CSS |
| PDF 导出 | WeasyPrint / Playwright print PDF |
| 证据截图 | PyMuPDF |
| 前端展示 | React / Streamlit |
| 人工复核 | React 表单 / Streamlit widgets |

## 11.7 建议负责人员与专业技能

| 角色 | 需要能力 |
|---|---|
| 前端工程师 | 报告 UI、PDF 预览、高亮证据 |
| 后端工程师 | 报告服务、文件导出、权限 |
| LLM 工程师 | 报告生成 Prompt、事实约束 |
| 金融专家 | 审核报告表达是否符合投研习惯 |

---

# 12. 模块 9：API 服务与原型系统交付层

## 12.1 模块定位

提供可运行的系统入口，支持输入公司名称、股票代码或招股书文件，输出风险提示和报告。

## 12.2 核心 API 设计

### 上传招股书

```http
POST /api/documents/upload
```

输入：

```json
{
  "company_id": "03750.HK",
  "document_type": "final_prospectus",
  "language": "zh",
  "file": "PDF 文件"
}
```

输出：

```json
{
  "document_id": "doc_03750_zh_final",
  "status": "uploaded"
}
```

### 启动分析任务

```http
POST /api/analysis/run
```

输入：

```json
{
  "company_id": "03750.HK",
  "analysis_type": "full_pipeline"
}
```

输出：

```json
{
  "run_id": "run_20260613_0001",
  "status": "running"
}
```

### 查询分析结果

```http
GET /api/analysis/{run_id}
```

输出：

```json
{
  "run_id": "run_20260613_0001",
  "status": "finished",
  "risk_score": 78.5,
  "risk_level": "high",
  "report_url": "/reports/run_20260613_0001.html"
}
```

### 查询证据

```http
GET /api/evidence/{risk_id}
```

输出：

```json
{
  "risk_id": "risk_001",
  "evidence": [
    {
      "page": 188,
      "text": "投资人享有特殊赎回权...",
      "screenshot_url": "/evidence/risk_001_page188.png"
    }
  ]
}
```

## 12.3 原型系统页面

| 页面 | 功能 |
|---|---|
| 首页 | 输入公司代码、上传招股书、选择分析任务 |
| 风险总览页 | 展示综合风险分、风险等级、核心诱因 |
| 证据溯源页 | 左侧风险结论，右侧 PDF 证据截图 |
| Agent 日志页 | 展示 Agent 调用流程、工具、输入输出 |
| 批量分析页 | 批量上传公司，导出风险排名表 |
| 回测评估页 | 展示高/中/低风险组回测结果 |

## 12.4 推荐技术栈

| 任务 | 推荐技术 |
|---|---|
| 后端 | FastAPI |
| 异步任务 | Celery / RQ |
| 数据库 | PostgreSQL |
| 向量库 | Qdrant |
| 文件存储 | MinIO / 本地文件系统 |
| 前端 MVP | Streamlit |
| 前端正式版 | React + Vite |
| 部署 | Docker Compose |

## 12.5 建议负责人员与专业技能

| 角色 | 需要能力 |
|---|---|
| 后端工程师 | FastAPI、数据库、异步任务、文件服务 |
| 前端工程师 | React/Streamlit、数据可视化、PDF 预览 |
| DevOps 工程师 | Docker、部署、环境配置 |
| 测试工程师 | 接口测试、端到端测试、异常场景 |

---

# 13. 模块 10：日志追踪与防幻觉评测层

## 13.1 模块定位

保证系统每个结论都可追踪，满足 Agent 推理链路、角色分工、工具调用记录和证据来源可追踪。

## 13.2 输入

每个 Agent、Skill、检索器、预测模型的运行记录。

## 13.3 输出

```json
{
  "run_id": "run_20260613_0001",
  "company_id": "03750.HK",
  "trace": [
    {
      "step": 1,
      "agent": "FinancialAgent",
      "tool": "cash_burn_skill",
      "input": {
        "table_id": "tbl_03750_000312"
      },
      "output": {
        "cash_runway_years": 0.9,
        "risk_level": "high"
      },
      "evidence_ids": ["tbl_03750_000312"],
      "timestamp": "2026-06-13T12:00:00"
    }
  ]
}
```

## 13.4 具体需要完成的工作

1. 记录每次模型调用。
2. 记录每次工具调用。
3. 记录检索 query 和返回的 evidence IDs。
4. 记录 Agent 输入输出。
5. 记录风险预测模型输入特征和输出分数。
6. 记录报告生成使用的风险点和证据。
7. 支持按公司、按 run_id、按 Agent 查询日志。

## 13.5 防幻觉机制

| 机制 | 做法 |
|---|---|
| 检索约束 | LLM 只能基于检索到的证据回答 |
| 证据强制 | 每个风险点必须绑定 page、section、text、bbox |
| Schema 约束 | Pydantic/JSON Schema 限制输出格式 |
| 数值复算 | 财务指标由 Python 计算 |
| Verifier 复核 | 检查结论是否被证据支持 |
| 无证据拒答 | 没证据时输出“未发现”或“需人工复核” |

## 13.6 推荐技术栈

| 任务 | 推荐技术 |
|---|---|
| 日志存储 | PostgreSQL JSONB |
| Trace ID | UUID / ULID |
| 可观测性 | OpenTelemetry，可选 |
| LLM 调用记录 | 自研 wrapper / LangSmith 思路 |
| 质量评测 | RAGAS 可选，自定义评测更重要 |

## 13.7 建议负责人员与专业技能

| 角色 | 需要能力 |
|---|---|
| 后端工程师 | 日志系统、数据库、查询接口 |
| LLM 工程师 | 防幻觉、Verifier、结构化输出 |
| 测试工程师 | 可追踪性测试、证据一致性测试 |

---

# 14. 模块 11：人工标注与评测层

## 14.1 模块定位

为赛题指标提供可验证依据，尤其是风险要素抽取准确率和证据片段召回率。

## 14.2 输入

```text
30–50 家人工标注公司
每家公司 10–20 个关键风险
总计约 300–1000 条风险标注
```

## 14.3 标注字段

```json
{
  "company_id": "03750.HK",
  "risk_type": "customer_concentration_risk",
  "risk_description": "前五大客户收入占比较高",
  "risk_level": "high",
  "evidence_page": 128,
  "evidence_text": "截至报告期，前五大客户收入占比为...",
  "is_key_risk": true,
  "requires_review": false
}
```

## 14.4 评测指标

### 风险抽取准确率

```text
准确率 = 系统抽取正确的风险数量 / 系统抽取出的风险总数量
```

### 证据召回率

```text
证据召回率 = 系统命中的人工标准证据数量 / 人工标准证据总数量
```

### Agent 可追踪率

```text
可追踪率 = 有完整 Agent/工具/证据日志的风险结论数量 / 全部风险结论数量
```

目标：

```text
关键风险要素抽取准确率 ≥ 80%
关键证据片段召回率 ≥ 85%
Agent 推理链路可追踪率 = 100%
```

## 14.5 推荐技术栈

| 任务 | 推荐技术 |
|---|---|
| 标注 | Label Studio / Excel / 自研页面 |
| 评测脚本 | Python、Pandas |
| 结果存储 | PostgreSQL / CSV |
| 可视化 | Streamlit / Plotly |

## 14.6 建议负责人员与专业技能

| 角色 | 需要能力 |
|---|---|
| 金融标注人员 | 招股书阅读、风险判断、证据定位 |
| 评测工程师 | 指标计算、抽样检查、一致性评估 |
| LLM 工程师 | 根据评测结果优化抽取链路 |

---

# 15. 推荐项目目录结构

```text
hk-ipo-riskagent/
├── README.md
├── pyproject.toml
├── docker-compose.yml
├── .env.example
├── configs/
│   ├── risk_taxonomy.yaml
│   ├── feature_rules.yaml
│   ├── agent_graph.yaml
│   └── model_config.yaml
├── data/
│   ├── raw/
│   │   ├── prospectus/
│   │   ├── company_info/
│   │   └── market_prices/
│   ├── processed/
│   └── gold_labels/
├── src/
│   ├── ingestion/
│   ├── parsing/
│   ├── retrieval/
│   ├── features/
│   ├── agents/
│   ├── skills/
│   ├── prediction/
│   ├── backtesting/
│   ├── reporting/
│   ├── api/
│   └── common/
├── notebooks/
│   ├── eda.ipynb
│   ├── feature_analysis.ipynb
│   └── backtest_analysis.ipynb
├── tests/
│   ├── test_pdf_parser.py
│   ├── test_feature_extraction.py
│   ├── test_agents.py
│   └── test_prediction.py
└── reports/
    ├── sample_report.md
    └── sample_report.html
```

---

# 16. 推荐实施路线

## 阶段 1：MVP，20–30 家公司

目标：能上传招股书，输出带证据的风险报告。

完成内容：

```text
1. PDF 解析
2. 章节切分
3. 基础证据检索
4. 10 个核心风险特征抽取
5. 规则评分卡
6. Markdown/HTML 报告生成
```

## 阶段 2：扩展到 100–200 家公司

目标：支持批量处理和回测。

完成内容：

```text
1. 批量数据接入
2. 特征表建设
3. 上市后 1/5/20/60 日标签计算
4. 高/中/低风险组回测
5. 风险抽取准确率和证据召回率评估
```

## 阶段 3：多 Agent 协同

目标：形成完整 Agentic AI Pipeline。

完成内容：

```text
1. LangGraph 编排
2. 财务 Agent
3. 法务 Agent
4. 业务 Agent
5. 市场 Agent
6. Verifier Agent
7. Agent 日志页面
```

## 阶段 4：正式参赛交付

目标：完整原型系统和可展示案例。

完成内容：

```text
1. FastAPI 服务
2. Web UI / Streamlit UI
3. 批量分析
4. 测试集预测结果表
5. Agent 推理日志
6. 典型案例报告
7. 环境配置与运行脚本
```

---

# 17. 最小可行技术栈组合

如果时间有限，建议采用以下组合：

| 模块       | MVP 技术                             |
| -------- | ---------------------------------- |
| 后端       | FastAPI                            |
| 原型界面     | Streamlit                          |
| 数据库      | PostgreSQL                         |
| 向量库      | Qdrant                             |
| PDF 解析   | PyMuPDF + pdfplumber               |
| OCR      | PaddleOCR，可选                       |
| Agent 编排 | LangGraph                          |
| LLM      | Qwen / DeepSeek / Claude / GPT，可配置 |
| 结构化输出    | Pydantic                           |
| 预测模型     | 规则评分卡 + LightGBM                   |
| 报告生成     | Markdown + Jinja2 + HTML           |
| 部署       | Docker Compose                     |
|          |                                    |

---

# 18. 建议团队分工

| 角色            |  人数建议 | 负责模块                       | 必备技能                                 |
| ------------- | ----: | -------------------------- | ------------------------------------ |
| 项目负责人 / 架构负责人 |     1 | 总体方案、进度、模块接口               | 系统设计、金融 AI 理解、工程协调                   |
| 金融投研/风控专家     |   1–2 | 风险 taxonomy、阈值、权重、案例复核     | 港股 IPO、财报分析、估值、破发逻辑                  |
| 法务/合规顾问       | 0.5–1 | 对赌、赎回、关联交易、诉讼合规            | 招股书法务条款、上市规则、合规审查                    |
| 数据工程师         |     1 | 数据接入、清洗、入库、批处理             | Python、Pandas、PostgreSQL、ETL         |
| 文档解析/NLP 工程师  |     1 | PDF 解析、OCR、表格抽取、chunk      | PyMuPDF、pdfplumber、OCR、NLP           |
| LLM/RAG 工程师   |     1 | 检索、防幻觉、结构化抽取               | RAG、Embedding、rerank、Prompt、Pydantic |
| Agent 工程师     |     1 | LangGraph、多 Agent、Skill 编排 | LangGraph、状态机、工具调用、日志追踪              |
| 数据科学/量化人员     |     1 | 风险预测、回测、评估指标               | LightGBM、XGBoost、SHAP、金融回测           |
| 后端工程师         |     1 | API、任务调度、服务化               | FastAPI、Celery、PostgreSQL、Docker     |
| 前端工程师         | 0.5–1 | 报告 UI、复核界面、可视化             | React/Streamlit、PDF 展示、图表            |
| 测试/评测工程师      |   0.5 | 指标评测、异常样本、端到端测试            | 测试设计、数据校验、指标计算                       |

如果团队人数有限，可以合并：

```text
后端 + 数据工程
LLM/RAG + Agent
数据科学 + 回测
金融专家 + 标注复核
```

---

# 19. 最终交付物清单

## 19.1 代码交付

```text
完整项目源代码
环境配置文件
Docker Compose
运行脚本
API 文档
测试脚本
```

## 19.2 数据交付

```text
公司主表
招股书解析结果
风险特征表
预测结果表
上市后表现标签表
人工标注 gold set
```

## 19.3 系统交付

```text
可运行原型系统
API 服务
单家公司分析入口
批量分析入口
报告导出功能
证据截图展示
Agent 日志展示
```

## 19.4 评测交付

```text
风险抽取准确率报告
证据召回率报告
Agent 可追踪率报告
上市后 5 日风险识别回测报告
典型案例分析报告
```

---

# 20. 最终方案一句话

本项目应实现为：

```text
以招股书 PDF 为核心证据源，结合港股基本信息和上市前市场行情，通过 PDF 解析、Hybrid RAG、风险特征抽取、多 Agent 协同、规则评分卡与机器学习回测，生成可解释、可追溯、可复核的港股 IPO 风险穿透预警报告。
```

项目最重要的竞争力不是“模型说它预测准”，而是：

```text
每一个风险判断都能找到原文证据，
每一个 Agent 结论都能追踪来源，
每一个预警结果都能用上市后表现回测验证。
```
