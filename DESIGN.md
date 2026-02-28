# Data Agent - Architecture Design

## Overview

Data Agent 是一个智能数据查询代理，能够同时接入结构化数据（Excel、CSV、数据库表）和非结构化数据（PDF、Word、文本文件等），根据用户的自然语言问题自动选择合适的查询技能，返回精准的答案。

**统一存储层**：使用 [MatrixOne](https://github.com/matrixorigin/matrixone) 作为唯一数据库，它是一个 HTAP 数据库，同时支持 OLTP/OLAP、向量搜索和全文检索，MySQL 协议兼容。一个数据库解决结构化查询、向量检索和元数据管理三个需求。

---

## Core Architecture

```
┌───────────────────────────────────────────────────────────────────────┐
│                          FastAPI Server                               │
│  ┌───────────┐    ┌──────────────┐    ┌────────────────────────────┐  │
│  │ Upload    │    │  Chat API    │    │  Data Source Mgmt API      │  │
│  │ API       │    │  (Query)     │    │  (Connect/Describe)        │  │
│  └─────┬─────┘    └──────┬───────┘    └──────────┬─────────────────┘  │
│        │                 │                       │                    │
│  ┌─────▼─────────────────▼───────────────────────▼─────────────────┐  │
│  │                   Ingestion Layer                               │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌────────────────────────┐  │  │
│  │  │ Excel/CSV   │  │ Document    │  │ External DB Connector  │  │  │
│  │  │ Loader      │  │ Loader      │  │ (MO already is the DB) │  │  │
│  │  └──────┬──────┘  └──────┬──────┘  └────────────┬───────────┘  │  │
│  └─────────┼────────────────┼──────────────────────┼──────────────┘  │
│            │                │                      │                  │
│            │         ┌──────▼───────┐              │                  │
│            │         │  Chunking +  │              │                  │
│            │         │  Embedding   │              │                  │
│            │         └──────┬───────┘              │                  │
│            │                │                      │                  │
│  ┌─────────▼────────────────▼──────────────────────▼──────────────┐  │
│  │                                                                │  │
│  │                     MatrixOne (unified)                        │  │
│  │                                                                │  │
│  │  ┌──────────────┐ ┌──────────────┐ ┌────────────────────────┐ │  │
│  │  │ 结构化表      │ │ 向量表       │ │ 元数据表               │ │  │
│  │  │ (用户上传的   │ │ (doc chunks  │ │ (data_sources,         │ │  │
│  │  │  Excel/CSV   │ │  + embeddings│ │  structured_meta,      │ │  │
│  │  │  → SQL表)    │ │  + fulltext) │ │  unstructured_meta)    │ │  │
│  │  └──────────────┘ └──────────────┘ └────────────────────────┘ │  │
│  │                                                                │  │
│  └────────────────────────────┬───────────────────────────────────┘  │
│                               │                                      │
│  ┌────────────────────────────▼───────────────────────────────────┐  │
│  │               Query Router (Claude)                            │  │
│  │   user question + metadata → skill selection + plan            │  │
│  └──────┬──────────────────┬──────────────────┬───────────────────┘  │
│         │                  │                  │                      │
│  ┌──────▼───────┐  ┌───────▼────────┐  ┌──────▼────────┐           │
│  │  SQL Skill   │  │  RAG Skill     │  │ Hybrid Skill  │           │
│  │  (text2sql)  │  │  (vector +     │  │ (SQL + RAG)   │           │
│  │              │  │   fulltext +   │  │               │           │
│  │              │  │   generate)    │  │               │           │
│  └──────┬───────┘  └───────┬────────┘  └──────┬────────┘           │
│         │                  │                  │                      │
│  ┌──────▼──────────────────▼──────────────────▼───────────────────┐  │
│  │               Result Synthesizer (Claude)                      │  │
│  │   raw results → human-readable answer (text/table/chart)       │  │
│  └────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────┘
```

**关键简化**: MatrixOne 同时承担结构化存储、向量存储和元数据存储三个角色，无需维护 DuckDB + ChromaDB + SQLite 三套系统。

---

## Component Design

### 1. MatrixOne Storage Layer（统一存储层）

MatrixOne 作为唯一数据库，通过 MySQL 协议（默认端口 6001）访问，使用 PyMySQL + SQLAlchemy 连接。

#### 1.1 结构化数据表
用户上传的 Excel/CSV 数据直接建表存储在 MatrixOne 中：
```sql
-- 用户上传 "Q4销售报表.xlsx" 后，系统自动建表
CREATE TABLE ds_q4_sales (
    product VARCHAR(255),
    region  VARCHAR(255),
    amount  DECIMAL(15,2),
    date    DATE
);
-- 批量插入数据
INSERT INTO ds_q4_sales VALUES (...), (...), ...;
```

#### 1.2 向量表（非结构化数据）
文档分块后，embedding 和原文一起存入 MatrixOne 的向量表：
```sql
CREATE TABLE doc_chunks (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    source_id   VARCHAR(36),        -- 关联 data_sources 表
    chunk_index INT,
    content     TEXT,                -- 原始文本块
    embedding   VECF32(1024),       -- voyage-3-large 输出 1024 维
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 创建向量索引 (HNSW)
-- MatrixOne 支持 IVF 和 HNSW 两种索引类型
CREATE INDEX idx_chunk_vec USING HNSW ON doc_chunks(embedding);

-- 创建全文索引
CREATE FULLTEXT INDEX idx_chunk_ft ON doc_chunks(content);
```

#### 1.3 元数据表
```sql
CREATE TABLE data_sources (
    id          VARCHAR(36) PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,     -- 用户可读名称 "Q4销售报表"
    type        VARCHAR(20) NOT NULL,      -- "structured" | "unstructured"
    source_type VARCHAR(20) NOT NULL,      -- "excel"|"csv"|"pdf"|"word"|"txt"
    description TEXT,                      -- 用户提供或 LLM 自动生成
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE structured_meta (
    source_id    VARCHAR(36) PRIMARY KEY,
    table_name   VARCHAR(255),             -- MatrixOne 中的实际表名
    columns_json JSON,                     -- 列信息 [{name, type, description, samples}]
    row_count    INT,
    sample_rows  JSON,                     -- 前 5 行样本数据
    FOREIGN KEY (source_id) REFERENCES data_sources(id)
);

CREATE TABLE unstructured_meta (
    source_id       VARCHAR(36) PRIMARY KEY,
    chunk_count     INT,
    doc_summary     TEXT,                  -- LLM 生成的文档摘要
    FOREIGN KEY (source_id) REFERENCES data_sources(id)
);
```

---

### 2. Ingestion Layer（数据接入层）

#### 2.1 Excel/CSV Loader
- 用 `openpyxl` 读取 Excel，`pandas` 处理 CSV
- 自动推断列类型（数值、文本、日期等），映射到 MySQL/MatrixOne 类型
- 在 MatrixOne 中 `CREATE TABLE` + 批量 `INSERT`
- 提取 schema 信息（列名、类型、示例值）写入 `structured_meta`
- 用 Claude 自动生成表描述和列描述，写入 `data_sources.description`

#### 2.2 Document Loader（非结构化）
- 支持 PDF（`pymupdf`）、Word（`python-docx`）、TXT
- 文档分块（chunking）：按段落/语义分块，chunk size 约 512 tokens，overlap 64 tokens
- 用 Voyage AI (`voyage-3-large`) 生成 1024 维 embedding
- 将 chunk 文本 + embedding 写入 `doc_chunks` 表
- 用 Claude 生成文档摘要，写入 `unstructured_meta.doc_summary`

#### 2.3 External Database
- MatrixOne 本身就是数据库，用户可以直接在 MatrixOne 中建表导入数据
- 如需接入外部 MySQL/PostgreSQL，可通过 MatrixOne 的联邦查询能力或 ETL 导入

---

### 3. Query Router（查询路由器）⭐ 核心组件

路由器决定了系统的精准度。设计为两阶段路由：

#### Stage 1: Data Source Selection（选数据源）
```
Input:  用户问题 + 所有数据源的元数据摘要
Output: 相关数据源列表 + 每个数据源的相关性评分
```

#### Stage 2: Skill Selection & Plan（选技能 + 生成执行计划）
```
Input:  用户问题 + 选中数据源的详细元数据（schema/摘要）
Output: 执行计划（用哪个 skill，具体参数）
```

**Router Prompt 设计**（精准路由的关键）：

```
你是一个数据查询路由器。根据用户问题和可用数据源，选择最合适的查询策略。

## 可用数据源
{metadata_registry_summary}

## 路由规则
1. 如果问题涉及"多少"、"平均"、"趋势"、"排名"、"对比"等量化分析 → SQL Skill
2. 如果问题涉及"什么是"、"解释"、"描述"、"为什么"等知识性问题 → RAG Skill
3. 如果问题同时需要数据查询和文档知识 → Hybrid Skill
4. 如果问题涉及多个数据源 → 标明所有涉及的数据源

## 用户问题
{user_question}

请返回 JSON 格式的执行计划。
```

**Router 返回格式**：
```json
{
  "skill": "sql | rag | hybrid",
  "data_sources": ["source_id_1", "source_id_2"],
  "reasoning": "选择该技能的理由",
  "sql_hint": "如果是SQL技能，给出查询思路",
  "search_query": "如果是RAG技能，给出检索关键词"
}
```

---

### 4. Skills（查询技能）

#### 4.1 SQL Skill（结构化数据查询）

**流程**: question → SQL generation → execution → result

```
Prompt 设计：
你是一个SQL专家。根据用户问题和表结构，生成准确的SQL查询。

## 表结构
{detailed_schema_with_samples}

## 注意事项
- 使用 MySQL 方言（MatrixOne 兼容 MySQL）
- 只生成 SELECT 查询（只读）
- 注意列名需用反引号包裹
- 如果涉及日期，使用标准 MySQL 日期函数

## 用户问题
{question}
```

**精准度保障机制**：
1. **Schema-aware**：提供完整的表结构 + 样本数据，让 Claude 了解数据长什么样
2. **Self-correction**：SQL 执行失败时，把错误信息反馈给 Claude 重新生成（最多重试 2 次）
3. **Result validation**：检查结果是否为空，为空时提示可能的原因

#### 4.2 RAG Skill（非结构化数据查询）

**流程**: question → vector search + fulltext search → rerank → generation

利用 MatrixOne 内置的向量搜索和全文检索，用一条 SQL 同时做两种检索：

```sql
-- 向量检索：语义相似度
SELECT id, content, source_id,
       l2_distance(embedding, %s) AS vec_dist
FROM doc_chunks
WHERE source_id IN (%s)
ORDER BY vec_dist ASC
LIMIT 20;

-- 全文检索：关键词匹配
SELECT id, content, source_id,
       MATCH(content) AGAINST (%s IN BOOLEAN MODE) AS ft_score
FROM doc_chunks
WHERE source_id IN (%s)
  AND MATCH(content) AGAINST (%s IN BOOLEAN MODE)
ORDER BY ft_score DESC
LIMIT 20;
```

**精准度保障机制**：
1. **双路检索**：向量搜索（语义）+ 全文检索（关键词），合并去重取 top 候选
2. **LLM Rerank**：用 Claude 对候选 chunks 做精排，选出 top-3 最相关的
3. **Citation**：答案中标注来源 chunk，方便用户验证
4. **Confidence**：如果检索到的内容与问题不太相关，明确告知用户

#### 4.3 Hybrid Skill（混合查询）

当问题同时需要结构化数据和非结构化知识时使用。

```
Step 1: 并行执行 SQL Skill 和 RAG Skill
Step 2: 将两者的结果合并
Step 3: 用 Claude 综合两方面信息，生成最终答案
```

---

### 5. Result Synthesizer（结果合成器）

将 Skill 的原始结果转化为用户友好的回答。

```python
class ResultSynthesizer:
    def synthesize(self, question: str, skill_results: list[SkillResult]) -> Response:
        # 根据结果类型选择输出格式
        # - 单个数值 → 直接文本回答
        # - 多行数据 → Markdown 表格
        # - 趋势数据 → 建议可视化 + 文本描述
        # - 文档知识 → 带引用的文本回答
```

---

## API Design

### Chat API（核心交互接口）
```
POST /api/chat
{
  "question": "上个季度销售额最高的产品是什么？",
  "session_id": "optional-session-id"
}

Response:
{
  "answer": "上个季度销售额最高的产品是...",
  "sources": [{"name": "Q4销售报表", "type": "excel"}],
  "skill_used": "sql",
  "sql_query": "SELECT product, SUM(amount) ...",
  "confidence": 0.95
}
```

### Data Source Management API
```
POST   /api/datasources/upload      # 上传文件（Excel/CSV/PDF/Word/TXT）
GET    /api/datasources             # 列出所有数据源
GET    /api/datasources/{id}        # 获取数据源详情（含schema/摘要）
PUT    /api/datasources/{id}        # 更新数据源描述
DELETE /api/datasources/{id}        # 删除数据源（同时删除对应的表/向量数据）
```

### 会话管理 API
```
POST   /api/sessions                # 创建新会话
GET    /api/sessions/{id}/history   # 获取会话历史
```

---

## Tech Stack

| Component        | Technology                    | Rationale                                          |
|------------------|-------------------------------|----------------------------------------------------|
| Web Framework    | FastAPI                       | 异步支持、自动API文档、类型检查                      |
| LLM              | Claude API (anthropic SDK)    | 用户指定                                            |
| **Database**     | **MatrixOne**                 | **HTAP + 向量搜索 + 全文检索，MySQL 兼容，一库搞定** |
| DB Driver        | PyMySQL + SQLAlchemy          | MatrixOne 兼容 MySQL 协议，用标准 MySQL 驱动即可      |
| Embedding        | Voyage AI (voyage-3-large)    | 高质量 embedding，1024 维，Anthropic 推荐             |
| Excel Parsing    | openpyxl + pandas             | 成熟稳定                                            |
| PDF Parsing      | PyMuPDF (fitz)                | 速度快、支持复杂布局                                  |
| Word Parsing     | python-docx                   | 标准 docx 解析                                      |

---

## Project Structure

```
data_agent/
├── pyproject.toml
├── DESIGN.md
├── src/
│   └── data_agent/
│       ├── __init__.py
│       ├── main.py                # FastAPI 入口
│       ├── config.py              # 配置管理（MO连接、API keys）
│       ├── api/
│       │   ├── __init__.py
│       │   ├── chat.py            # Chat 路由
│       │   ├── datasource.py      # 数据源管理路由
│       │   └── schemas.py         # Pydantic request/response models
│       ├── ingestion/
│       │   ├── __init__.py
│       │   ├── excel_loader.py    # Excel/CSV → MatrixOne 表
│       │   └── doc_loader.py      # PDF/Word/TXT → 分块 + embedding → MatrixOne 向量表
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── database.py        # MatrixOne 连接池管理（SQLAlchemy engine）
│       │   └── metadata.py        # Metadata CRUD（操作 data_sources 等元数据表）
│       ├── router/
│       │   ├── __init__.py
│       │   └── query_router.py    # 两阶段查询路由
│       ├── skills/
│       │   ├── __init__.py
│       │   ├── base.py            # Skill 基类
│       │   ├── sql_skill.py       # Text-to-SQL（生成 MySQL 兼容 SQL）
│       │   ├── rag_skill.py       # 向量检索 + 全文检索 + 生成
│       │   └── hybrid_skill.py    # 混合查询
│       └── llm/
│           ├── __init__.py
│           └── claude_client.py   # Claude API 封装
├── tests/
│   ├── __init__.py
│   ├── test_ingestion.py
│   ├── test_router.py
│   ├── test_skills.py
│   └── test_api.py
├── data/
│   └── uploads/                   # 用户上传的原始文件备份
└── .env.example                   # 环境变量模板
```

**对比之前的结构变化**：
- 删除 `storage/structured.py`（DuckDB）和 `storage/vector.py`（ChromaDB）
- 合并为 `storage/database.py`（MatrixOne 统一管理）
- 删除 `ingestion/db_connector.py`（MatrixOne 本身就是目标数据库）
- `data/` 目录简化，只保留 uploads（所有持久化数据都在 MatrixOne 中）

---

## Key Design Decisions

### 1. 为什么用 MatrixOne 统一存储？

| 对比项           | 之前（三件套）              | 现在（MatrixOne）                    |
|-----------------|---------------------------|--------------------------------------|
| 结构化查询       | DuckDB                    | MatrixOne (MySQL SQL)                |
| 向量搜索         | ChromaDB                  | MatrixOne (内置 HNSW/IVF)           |
| 全文检索         | 无                        | MatrixOne (内置 FULLTEXT INDEX)      |
| 元数据           | SQLite                    | MatrixOne (同一个数据库)             |
| 运维复杂度       | 3 个存储引擎               | 1 个数据库                           |
| 数据一致性       | 需要跨存储协调             | 单库事务保证                         |
| SQL 方言         | DuckDB SQL                | MySQL（LLM 更熟悉，生成更准确）      |

额外收益：
- MySQL 方言是 LLM 训练数据中最常见的 SQL 方言，text-to-SQL 准确率更高
- 向量搜索和结构化查询在同一个数据库中，未来可以做 JOIN 查询
- 部署简化，只需一个数据库服务

### 2. 为什么路由器设计为两阶段？
- 第一阶段只看元数据摘要，快速缩小范围，节省 token
- 第二阶段看选中数据源的详细 schema，生成精准执行计划
- 避免把所有 schema 塞进一个 prompt 导致信息过载

### 3. 精准度提升策略总结
| 策略                     | 作用                                    |
|-------------------------|-----------------------------------------|
| 元数据丰富描述            | 帮助路由器精准选择数据源                  |
| 两阶段路由                | 先粗筛后精选，减少噪音                   |
| Schema + 样本数据         | 让 SQL 生成更准确                       |
| MySQL 方言               | LLM 最熟悉的 SQL 方言，准确率更高        |
| SQL Self-correction      | 执行失败自动重试修正                     |
| 向量 + 全文双路检索       | 语义匹配 + 关键词匹配互补                |
| LLM Rerank              | 粗检索后精排，提升相关性                  |
| 结果置信度               | 低置信度时主动告知用户，避免误导           |

### 4. 安全性考虑
- SQL Skill 只生成 SELECT 语句
- MatrixOne 可配置只读用户权限
- 上传文件做类型和大小校验
- API 预留认证中间件接口

---

## Execution Flow Example

**用户问题**: "上个季度哪个产品的销售额最高？"

```
1. [Router Stage 1]
   - 查询 data_sources 表：发现有 "Q4销售报表"(structured) 和 "产品手册"(unstructured)
   - 判断：问题是量化分析，涉及 "销售额"、"最高" → 匹配 "Q4销售报表"

2. [Router Stage 2]
   - 查询 structured_meta：列有 product, region, amount, date
   - 确定：使用 SQL Skill，目标表 ds_q4_sales

3. [SQL Skill]
   - 生成 SQL:
     SELECT `product`, SUM(`amount`) AS total
     FROM ds_q4_sales
     WHERE `date` >= '2025-10-01'
     GROUP BY `product`
     ORDER BY total DESC
     LIMIT 1;
   - 在 MatrixOne 执行，得到结果: {"product": "产品A", "total": 1500000}

4. [Result Synthesizer]
   - 生成回答: "上个季度销售额最高的产品是**产品A**，总销售额为 150 万元。"
```

**用户问题**: "这个产品的核心卖点是什么？"（接上文）

```
1. [Router] 识别"这个产品"指"产品A"（上下文），问题是知识性 → RAG Skill
2. [RAG Skill]
   - Embedding "产品A 核心卖点" → 向量
   - 在 MatrixOne 中执行向量检索 + 全文检索
   - 从 doc_chunks 中找到"产品手册"相关段落
   - Claude rerank → 选出 top-3 chunks
   - Claude 生成答案并标注来源
3. [Result] "产品A 的核心卖点包括：1) ... 2) ... [来源: 产品手册 P.12]"
```

---

## MatrixOne Connection Config

```python
# .env
MO_HOST=127.0.0.1
MO_PORT=6001
MO_USER=root
MO_PASSWORD=111
MO_DATABASE=data_agent

# SQLAlchemy connection string
# mysql+pymysql://root:111@127.0.0.1:6001/data_agent
```

---

## Phase Plan

### Phase 1 - MVP（结构化数据可用）
- [x] 项目骨架搭建
- [ ] MatrixOne 连接管理 + 元数据表初始化
- [ ] Excel/CSV 上传与加载到 MatrixOne
- [ ] Metadata Registry CRUD
- [ ] Query Router（单阶段简化版）
- [ ] SQL Skill（text-to-SQL + 执行 + self-correction）
- [ ] Chat API + Result Synthesizer
- [ ] 基本错误处理

### Phase 2 - RAG 能力
- [ ] PDF/Word/TXT 文档上传
- [ ] 文档分块 + Voyage AI Embedding
- [ ] MatrixOne 向量表 + HNSW 索引 + 全文索引
- [ ] RAG Skill（双路检索 + LLM rerank）
- [ ] 两阶段路由升级
- [ ] Hybrid Skill

### Phase 3 - 生产加固
- [ ] 会话历史与上下文记忆
- [ ] 认证与权限
- [ ] 前端界面
- [ ] 监控与日志
- [ ] MatrixOne 高可用部署
