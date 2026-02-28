# Data Agent - Architecture Design

## Overview

Data Agent 是一个智能数据查询代理，能够同时接入结构化数据（Excel、CSV、数据库表）和非结构化数据（PDF、Word、文本文件等），根据用户的自然语言问题自动选择合适的查询技能，返回精准的答案。

---

## Core Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        FastAPI Server                            │
│  ┌──────────┐    ┌──────────────┐    ┌────────────────────────┐  │
│  │ Upload   │    │  Chat API    │    │  Data Source Mgmt API  │  │
│  │ API      │    │  (Query)     │    │  (Connect/Describe)    │  │
│  └────┬─────┘    └──────┬───────┘    └──────────┬─────────────┘  │
│       │                 │                       │                │
│  ┌────▼─────────────────▼───────────────────────▼─────────────┐  │
│  │                  Ingestion Layer                            │  │
│  │  ┌────────────┐ ┌────────────┐ ┌──────────────────────┐    │  │
│  │  │Excel/CSV   │ │ Document   │ │ DB Connector         │    │  │
│  │  │Loader      │ │ Loader     │ │ (MySQL/PG/SQLite)    │    │  │
│  │  └─────┬──────┘ └─────┬──────┘ └──────────┬───────────┘    │  │
│  └────────┼──────────────┼───────────────────┼────────────────┘  │
│           │              │                   │                   │
│  ┌────────▼──────────────▼───────────────────▼────────────────┐  │
│  │              Metadata Registry                             │  │
│  │  (data source name, type, schema, description, stats)      │  │
│  └────────────────────────┬───────────────────────────────────┘  │
│                           │                                      │
│  ┌────────────────────────▼───────────────────────────────────┐  │
│  │              Query Router (Claude)                         │  │
│  │  user question + metadata → skill selection + plan         │  │
│  └──────┬─────────────────┬──────────────────┬────────────────┘  │
│         │                 │                  │                   │
│  ┌──────▼──────┐  ┌───────▼───────┐  ┌───────▼───────┐         │
│  │  SQL Skill  │  │  RAG Skill    │  │ Hybrid Skill  │         │
│  │  (text2sql) │  │  (retrieve+   │  │ (SQL + RAG)   │         │
│  │             │  │   generate)   │  │               │         │
│  └──────┬──────┘  └───────┬───────┘  └───────┬───────┘         │
│         │                 │                  │                   │
│  ┌──────▼─────────────────▼──────────────────▼────────────────┐  │
│  │              Result Synthesizer (Claude)                   │  │
│  │  raw results → human-readable answer (text/table/chart)    │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘

Storage Layer:
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│    DuckDB        │  │   ChromaDB       │  │   SQLite         │
│  (structured     │  │  (vector store   │  │  (metadata       │
│   query engine)  │  │   for RAG)       │  │   registry)      │
└──────────────────┘  └──────────────────┘  └──────────────────┘
```

---

## Component Design

### 1. Ingestion Layer（数据接入层）

负责将各种数据源统一接入到系统中。

#### 1.1 Excel/CSV Loader
- 用 `openpyxl` 读取 Excel，`pandas` 处理 CSV
- 自动推断列类型（数值、文本、日期等）
- 将数据加载到 DuckDB 中作为可查询的表
- 提取 schema 信息（列名、类型、示例值）注册到 Metadata Registry

#### 1.2 Document Loader（非结构化）
- 支持 PDF（`pymupdf`）、Word（`python-docx`）、TXT
- 文档分块（chunking）：按段落/语义分块，chunk size 约 512 tokens，overlap 64 tokens
- 用 `voyageai` 或 Claude 内置能力做 embedding
- 存入 ChromaDB 向量库

#### 1.3 Database Connector
- 支持 MySQL、PostgreSQL、SQLite 等关系型数据库
- 通过 SQLAlchemy 连接，只读模式
- 自动探测 schema（表名、列名、类型、外键关系）
- 注册到 Metadata Registry（不复制数据，查询时直连）

---

### 2. Metadata Registry（元数据注册中心）

这是精准路由的核心。存储在 SQLite 中。

```python
@dataclass
class DataSource:
    id: str                    # UUID
    name: str                  # 用户可读名称，如 "Q4销售报表"
    type: str                  # "structured" | "unstructured"
    source_type: str           # "excel" | "csv" | "database" | "pdf" | "word" | "txt"
    description: str           # 用户提供或 LLM 自动生成的描述
    created_at: datetime

@dataclass
class StructuredMeta:
    source_id: str
    table_name: str            # DuckDB/外部DB中的表名
    columns: list[ColumnInfo]  # 列名、类型、描述、示例值
    row_count: int
    sample_rows: list[dict]    # 前几行样本数据
    connection_type: str       # "duckdb" | "external"
    connection_string: str     # 外部DB连接串（加密存储）

@dataclass
class UnstructuredMeta:
    source_id: str
    collection_name: str       # ChromaDB中的collection名
    chunk_count: int
    doc_summary: str           # LLM生成的文档摘要
```

**关键设计**：用户上传数据时，系统会用 Claude 自动生成 `description` 和列描述，这些描述帮助 Router 精准匹配数据源。

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
  "skill": "sql" | "rag" | "hybrid",
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
- 使用 DuckDB SQL 方言
- 只生成 SELECT 查询（只读）
- 注意列名的大小写和空格
- 如果涉及日期，注意日期格式

## 用户问题
{question}
```

**精准度保障机制**：
1. **Schema-aware**：提供完整的表结构 + 样本数据，让 Claude 了解数据长什么样
2. **Self-correction**：SQL 执行失败时，把错误信息反馈给 Claude 重新生成（最多重试 2 次）
3. **Result validation**：检查结果是否为空，为空时提示可能的原因

#### 4.2 RAG Skill（非结构化数据查询）

**流程**: question → embedding → vector search → rerank → context assembly → generation

```
Step 1: 将用户问题转成 embedding
Step 2: 在 ChromaDB 中检索 top-K 相关 chunks（K=10）
Step 3: 用 Claude 对 chunks 做 rerank，选出 top-3 最相关的
Step 4: 将相关 chunks 作为 context，用 Claude 生成答案
```

**精准度保障机制**：
1. **Rerank**：粗检索后用 LLM 精排，大幅提升相关性
2. **Citation**：答案中标注来源 chunk，方便用户验证
3. **Confidence**：如果检索到的内容与问题不太相关，明确告知用户

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
  "sql_query": "SELECT product, SUM(amount) ...",  // 如果用了SQL
  "confidence": 0.95
}
```

### Data Source Management API
```
POST   /api/datasources/upload      # 上传文件（Excel/CSV/PDF/Word/TXT）
POST   /api/datasources/connect     # 连接外部数据库
GET    /api/datasources             # 列出所有数据源
GET    /api/datasources/{id}        # 获取数据源详情
PUT    /api/datasources/{id}        # 更新数据源描述
DELETE /api/datasources/{id}        # 删除数据源
```

### 会话管理 API
```
POST   /api/sessions                # 创建新会话
GET    /api/sessions/{id}/history   # 获取会话历史
```

---

## Tech Stack

| Component        | Technology                    | Rationale                              |
|------------------|-------------------------------|----------------------------------------|
| Web Framework    | FastAPI                       | 异步支持、自动API文档、类型检查         |
| LLM              | Claude API (anthropic SDK)    | 用户指定                                |
| Structured Store | DuckDB                        | 轻量嵌入式、支持直接查询CSV/Parquet、SQL兼容 |
| Vector Store     | ChromaDB                      | 轻量嵌入式、Python原生、易部署          |
| Embedding        | Voyage AI (voyage-3-large)    | 高质量embedding、Anthropic推荐          |
| Metadata Store   | SQLite (via SQLAlchemy)       | 轻量、无需额外服务                      |
| Excel Parsing    | openpyxl + pandas             | 成熟稳定                                |
| PDF Parsing      | PyMuPDF (fitz)                | 速度快、支持复杂布局                     |
| Word Parsing     | python-docx                   | 标准docx解析                            |
| DB Connector     | SQLAlchemy                    | 统一多数据库访问                        |

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
│       ├── config.py              # 配置管理
│       ├── api/
│       │   ├── __init__.py
│       │   ├── chat.py            # Chat 路由
│       │   ├── datasource.py      # 数据源管理路由
│       │   └── schemas.py         # Pydantic request/response models
│       ├── ingestion/
│       │   ├── __init__.py
│       │   ├── excel_loader.py    # Excel/CSV 加载器
│       │   ├── doc_loader.py      # PDF/Word/TXT 加载器
│       │   └── db_connector.py    # 外部数据库连接器
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── metadata.py        # Metadata Registry（SQLite）
│       │   ├── structured.py      # DuckDB 管理
│       │   └── vector.py          # ChromaDB 管理
│       ├── router/
│       │   ├── __init__.py
│       │   └── query_router.py    # 两阶段查询路由
│       ├── skills/
│       │   ├── __init__.py
│       │   ├── base.py            # Skill 基类
│       │   ├── sql_skill.py       # Text-to-SQL
│       │   ├── rag_skill.py       # RAG 检索增强生成
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
├── data/                          # 运行时数据目录
│   ├── uploads/                   # 用户上传文件
│   ├── duckdb/                    # DuckDB 数据文件
│   ├── chroma/                    # ChromaDB 持久化
│   └── metadata.db                # SQLite 元数据
└── .env.example                   # 环境变量模板
```

---

## Key Design Decisions

### 1. 为什么用 DuckDB 而不是直接用 pandas？
- DuckDB 是嵌入式分析型数据库，支持标准 SQL
- Text-to-SQL 生成 SQL 比生成 pandas 代码更可靠、更安全（只读 SELECT）
- 性能优秀，大数据集上比 pandas 快很多
- 可以直接查询 CSV/Parquet 文件

### 2. 为什么路由器设计为两阶段？
- 第一阶段只看元数据摘要，快速缩小范围，节省 token
- 第二阶段看选中数据源的详细 schema，生成精准执行计划
- 避免把所有 schema 塞进一个 prompt 导致信息过载

### 3. 精准度提升策略总结
| 策略                  | 作用                                    |
|-----------------------|-----------------------------------------|
| 元数据丰富描述         | 帮助路由器精准选择数据源                  |
| 两阶段路由             | 先粗筛后精选，减少噪音                   |
| Schema + 样本数据      | 让 SQL 生成更准确                       |
| SQL Self-correction    | 执行失败自动重试修正                     |
| RAG Rerank            | 粗检索后 LLM 精排，提升相关性            |
| 结果置信度            | 低置信度时主动告知用户，避免误导          |

### 4. 安全性考虑
- SQL Skill 只生成 SELECT 语句，在 DuckDB 中用只读模式执行
- 外部数据库连接串加密存储
- 上传文件做类型和大小校验
- API 预留认证中间件接口

---

## Execution Flow Example

**用户问题**: "上个季度哪个产品的销售额最高？"

```
1. [Router Stage 1]
   - 读取 Metadata Registry：发现有 "Q4销售报表"(Excel/structured) 和 "产品手册"(PDF/unstructured)
   - 判断：问题是量化分析，涉及 "销售额"、"最高" → 匹配 "Q4销售报表"

2. [Router Stage 2]
   - 读取 "Q4销售报表" 详细 schema：列有 product, region, amount, date
   - 确定：使用 SQL Skill，目标表 q4_sales

3. [SQL Skill]
   - 生成 SQL: SELECT product, SUM(amount) as total FROM q4_sales
                WHERE date >= '2025-10-01' GROUP BY product ORDER BY total DESC LIMIT 1
   - 在 DuckDB 执行，得到结果: {"product": "产品A", "total": 1500000}

4. [Result Synthesizer]
   - 生成回答: "上个季度销售额最高的产品是**产品A**，总销售额为 150 万元。"
```

---

## Phase Plan

### Phase 1 - MVP（核心可用）
- [x] 项目骨架搭建
- [ ] Excel/CSV 上传与加载到 DuckDB
- [ ] Metadata Registry
- [ ] Query Router（单阶段简化版）
- [ ] SQL Skill（text-to-SQL + 执行）
- [ ] Chat API
- [ ] 基本错误处理

### Phase 2 - RAG 能力
- [ ] PDF/Word/TXT 文档上传
- [ ] 文档分块 + Embedding + ChromaDB 存储
- [ ] RAG Skill
- [ ] 两阶段路由升级
- [ ] Hybrid Skill

### Phase 3 - 生产加固
- [ ] 外部数据库连接
- [ ] 会话历史与上下文
- [ ] 认证与权限
- [ ] 前端界面
- [ ] 监控与日志
