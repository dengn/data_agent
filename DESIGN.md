# Data Agent - Architecture Design

## Overview

Data Agent 是一个智能数据查询代理，能够同时接入结构化数据（Excel、CSV、数据库表）和非结构化数据（PDF、Word、文本文件等），根据用户的自然语言问题自动选择合适的查询技能，返回精准的答案。

**平台选型**：
- **[MatrixOne](https://github.com/matrixorigin/matrixone)** — HTAP 数据库，MySQL 兼容，内置向量搜索 + 全文检索，作为统一存储层
- **[MatrixOne Intelligence (MOI)](https://docs.matrixorigin.cn/zh/m1intelligence/)** — AI 原生数据智能平台，提供非结构化数据处理全流程 API（文档解析 → 分段 → Embedding → 向量存储），免去自建解析/分块/嵌入管线
- **Claude API** — 查询路由、Text-to-SQL、RAG 生成、结果合成

---

## Core Architecture

```
┌───────────────────────────────────────────────────────────────────────────┐
│                           FastAPI Server                                  │
│  ┌───────────┐    ┌──────────────┐    ┌────────────────────────────────┐  │
│  │ Upload    │    │  Chat API    │    │  Data Source Mgmt API          │  │
│  │ API       │    │  (Query)     │    │  (Connect/Describe)            │  │
│  └─────┬─────┘    └──────┬───────┘    └──────────┬─────────────────────┘  │
│        │                 │                       │                        │
│  ┌─────▼─────────────────▼───────────────────────▼─────────────────────┐  │
│  │                    Ingestion Layer                                  │  │
│  │                                                                     │  │
│  │  ┌─────────────────────────────────────────────────────────────┐    │  │
│  │  │  Excel/CSV → pandas 解析 → MatrixOne CREATE TABLE + INSERT │    │  │
│  │  └─────────────────────────┬───────────────────────────────────┘    │  │
│  │                            │                                        │  │
│  │  ┌─────────────────────────▼───────────────────────────────────┐    │  │
│  │  │  PDF/Word/TXT/图片/音视频 → MOI Workflow API                │    │  │
│  │  │  (上传 → 文档解析 → 分段 → Embedding → 向量存储)            │    │  │
│  │  │  全部由 MOI 平台处理，无需本地解析库                          │    │  │
│  │  └─────────────────────────┬───────────────────────────────────┘    │  │
│  └────────────────────────────┼────────────────────────────────────────┘  │
│                               │                                           │
│  ┌────────────────────────────▼────────────────────────────────────────┐  │
│  │                                                                     │  │
│  │                      MatrixOne (unified DB)                         │  │
│  │                                                                     │  │
│  │  ┌──────────────┐  ┌──────────────────────┐  ┌──────────────────┐  │  │
│  │  │ 结构化数据表  │  │ MOI 处理结果         │  │ 元数据表          │  │  │
│  │  │ (Excel/CSV → │  │ (doc chunks +        │  │ (data_sources,   │  │  │
│  │  │  SQL表)      │  │  embedding + 全文索引 │  │  structured_meta │  │  │
│  │  │              │  │  由 MOI workflow 写入) │  │  unstructured_   │  │  │
│  │  │              │  │                       │  │  meta)           │  │  │
│  │  └──────────────┘  └──────────────────────┘  └──────────────────┘  │  │
│  │                                                                     │  │
│  └────────────────────────────┬────────────────────────────────────────┘  │
│                               │                                           │
│  ┌────────────────────────────▼────────────────────────────────────────┐  │
│  │                Query Router (Claude)                                │  │
│  │    user question + metadata → skill selection + plan               │  │
│  └──────┬──────────────────┬──────────────────────┬───────────────────┘  │
│         │                  │                      │                      │
│  ┌──────▼───────┐  ┌───────▼──────────┐  ┌───────▼────────┐            │
│  │  SQL Skill   │  │  RAG Skill       │  │ Hybrid Skill   │            │
│  │  (text2sql   │  │  (MO向量检索 +   │  │ (SQL + RAG     │            │
│  │   → MO执行)  │  │   MO全文检索 +   │  │  并行执行)     │            │
│  │              │  │   Claude 生成)    │  │                │            │
│  └──────┬───────┘  └───────┬──────────┘  └───────┬────────┘            │
│         │                  │                      │                      │
│  ┌──────▼──────────────────▼──────────────────────▼───────────────────┐  │
│  │                Result Synthesizer (Claude)                         │  │
│  │    raw results → human-readable answer (text/table/chart)         │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## Component Design

### 1. Ingestion Layer（数据接入层）

#### 1.1 结构化数据：Excel/CSV Loader（本地处理）

Excel/CSV 是结构化表格，处理逻辑简单，本地用 pandas 解析后直接写入 MatrixOne：

```python
# 1. pandas 读取 + 类型推断
df = pd.read_excel(file)  # 或 pd.read_csv(file)

# 2. 映射到 MySQL 类型，在 MatrixOne 中建表
CREATE TABLE ds_{sanitized_name} (
    col1 VARCHAR(255),
    col2 DECIMAL(15,2),
    col3 DATE,
    ...
);

# 3. 批量插入
INSERT INTO ds_{sanitized_name} VALUES (...), (...), ...;

# 4. 提取 schema + 样本数据 → 写入 structured_meta
# 5. Claude 自动生成表描述和列描述 → 写入 data_sources.description
```

#### 1.2 非结构化数据：MOI Workflow API（平台处理）⭐ 关键简化

**之前的方案**：本地用 PyMuPDF 解析 PDF、python-docx 解析 Word、手写分块逻辑、调用 Voyage AI embedding — 代码量大、效果难保证。

**现在的方案**：调用 MOI 平台的 Workflow API，整个管线由平台完成：

```
用户上传文件 → MOI 文档解析（OCR/表格识别/公式解析）
             → MOI 智能分段（层级感知，避免跨章节）
             → MOI 文本嵌入（bge-m3 模型）
             → 结果存入 MatrixOne 向量表
```

**具体 API 调用流程**：

```python
# Step 0: 认证（获取 moi-key）
# POST /user/me/api-key
# Headers: access-token, uid

# Step 1: 创建数据卷（存放原始文件）
POST /catalog/volume/create
Body: {"database_id": "...", "name": "raw_docs", "description": "原始文档"}

# Step 2: 上传文件到连接器
POST /connectors/upload
Content-Type: multipart/form-data
Body: file + VolumeID + meta[{file_name, file_size, mime_type}]

# Step 3: 创建数据加载任务（文件 → 原始数据卷）
POST /task
Body: {
    "source_connector_id": "...",
    "volume_id": "...",
    "source_config": {...},
    "config_type": "..."
}

# Step 4: 创建工作流（解析 → 分段 → 嵌入）
POST /byoa/api/v1/workflow_meta
Body: {
    "name": "doc_processing",
    "source_volume_ids": ["..."],
    "target_volume_ids": "...",
    "file_types": [2],  // PDF
    "process_mode": {"type": 0},  // 单次
    "workflow": {
        // DAG 节点配置：
        // 文档解析节点 → 分段节点 → 文本嵌入节点
    }
}
# 也可以直接使用 MOI 预置的「图文混合文档 RAG 数据准备」模板

# Step 5: 查询处理结果（分块 + embedding）
POST /byoa/api/v1/explore/volumes/{volume_id}/files/{file_id}/blocks
Body: {"list_embedding": true, "offset": 0, "limit": 100}
# 返回: [{id, content, content_type, embedding, ...}]
```

**MOI 支持的文件格式**：PDF、DOCX、PPTX、TXT、MD、CSV、Excel、HTML、JPG/PNG/GIF/WebP、音频文件。

**MOI 工作流的 9 个处理节点**：

| 节点           | 功能                                                  |
|---------------|-------------------------------------------------------|
| 文档解析       | OCR、表格识别、公式→Markdown、标题层级识别              |
| 图片解析       | 图片文字识别 + 视觉内容描述                            |
| 音频解析       | SenseVoice 语音转写                                   |
| 视频解析       | 视频音轨分离 + 转写                                   |
| 分段           | 智能分段（按标志符/长度/自定义），层级感知避免跨章节    |
| 文本嵌入       | bge-m3 模型生成向量                                   |
| 信息提取       | LLM + JSON Schema 结构化提取（支持多文件合并提取）     |
| 数据清洗       | 去重、敏感信息打码、文本标准化                         |
| 数据增强       | 生成训练样本（Alpaca/ShareGPT/OpenAI 格式）            |

**对比本地方案的优势**：

| 对比项         | 本地方案                              | MOI 方案                                |
|---------------|---------------------------------------|----------------------------------------|
| PDF 解析       | PyMuPDF（纯文本，复杂版式效果差）      | 多模态模型（OCR + 表格 + 公式）         |
| 分段           | 固定 chunk size + overlap             | 层级感知分段，不跨章节                   |
| Embedding      | Voyage AI（需额外 API key + 费用）    | bge-m3（平台内置，一体化）              |
| 图片/音视频    | 不支持                                | 原生支持                                |
| 代码量         | ~500 行解析/分块/嵌入代码             | ~50 行 API 调用代码                     |
| 维护成本       | 需跟进各解析库版本                     | 平台升级自动获得最新能力                 |

---

### 2. MatrixOne Storage Layer（统一存储层）

MatrixOne 通过 MySQL 协议（默认端口 6001）访问，使用 PyMySQL + SQLAlchemy 连接。

#### 2.1 结构化数据表
```sql
-- 用户上传 "Q4销售报表.xlsx" 后，系统自动建表
CREATE TABLE ds_q4_sales (
    product VARCHAR(255),
    region  VARCHAR(255),
    amount  DECIMAL(15,2),
    date    DATE
);
INSERT INTO ds_q4_sales VALUES (...), (...), ...;
```

#### 2.2 向量数据
MOI Workflow 处理完文档后，分块和 embedding 结果存储在 MatrixOne 中。可以直接通过 SQL 进行向量检索和全文检索：

```sql
-- 向量检索：语义相似度
SELECT id, content, source_id,
       l2_distance(embedding, '[0.1, 0.2, ...]') AS vec_dist
FROM doc_chunks
WHERE source_id IN ('source_1', 'source_2')
ORDER BY vec_dist ASC
LIMIT 20;

-- 全文检索：关键词匹配
SELECT id, content, source_id,
       MATCH(content) AGAINST ('关键词' IN BOOLEAN MODE) AS ft_score
FROM doc_chunks
WHERE MATCH(content) AGAINST ('关键词' IN BOOLEAN MODE)
ORDER BY ft_score DESC
LIMIT 20;
```

#### 2.3 元数据表
```sql
CREATE TABLE data_sources (
    id          VARCHAR(36) PRIMARY KEY,
    name        VARCHAR(255) NOT NULL,       -- "Q4销售报表"
    type        VARCHAR(20) NOT NULL,        -- "structured" | "unstructured"
    source_type VARCHAR(20) NOT NULL,        -- "excel"|"csv"|"pdf"|"word"|"txt"
    description TEXT,                        -- 用户提供或 LLM 自动生成
    moi_volume_id  VARCHAR(64),              -- MOI 数据卷 ID（非结构化）
    moi_workflow_id VARCHAR(64),             -- MOI 工作流 ID（非结构化）
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE structured_meta (
    source_id    VARCHAR(36) PRIMARY KEY,
    table_name   VARCHAR(255),               -- MatrixOne 中的实际表名
    columns_json JSON,                       -- [{name, type, description, samples}]
    row_count    INT,
    sample_rows  JSON,                       -- 前 5 行样本数据
    FOREIGN KEY (source_id) REFERENCES data_sources(id)
);

CREATE TABLE unstructured_meta (
    source_id       VARCHAR(36) PRIMARY KEY,
    chunk_count     INT,
    doc_summary     TEXT,                    -- LLM 生成的文档摘要
    FOREIGN KEY (source_id) REFERENCES data_sources(id)
);
```

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

**Router Prompt 设计**：

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

**流程**: question → SQL generation (Claude) → MatrixOne 执行 → result

```
Prompt：
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

**精准度保障**：
1. **Schema-aware**：完整表结构 + 样本数据
2. **Self-correction**：SQL 执行失败时带错误信息重试（最多 2 次）
3. **Result validation**：结果为空时提示可能原因

#### 4.2 RAG Skill（非结构化数据查询）

**流程**: question → embedding → MO 向量检索 + MO 全文检索 → Claude rerank → Claude 生成

```python
# 1. 将用户问题转成 embedding（调用 MOI 的 embedding 能力或直接用 bge-m3）
query_embedding = get_embedding(question)

# 2. 在 MatrixOne 中做向量检索
vector_results = mo.execute("""
    SELECT content, l2_distance(embedding, %s) AS dist
    FROM doc_chunks WHERE source_id IN (%s)
    ORDER BY dist ASC LIMIT 20
""", [query_embedding, source_ids])

# 3. 在 MatrixOne 中做全文检索
fulltext_results = mo.execute("""
    SELECT content, MATCH(content) AGAINST (%s IN BOOLEAN MODE) AS score
    FROM doc_chunks WHERE source_id IN (%s)
    AND MATCH(content) AGAINST (%s IN BOOLEAN MODE)
    ORDER BY score DESC LIMIT 20
""", [keywords, source_ids, keywords])

# 4. 合并去重 → Claude rerank → 选 top-3
# 5. Claude 基于 top-3 chunks 生成答案（带引用）
```

**精准度保障**：
1. **双路检索**：语义 + 关键词互补
2. **LLM Rerank**：粗检索后精排
3. **Citation**：答案标注来源
4. **Confidence**：低相关度时主动告知

#### 4.3 Hybrid Skill（混合查询）

```
Step 1: 并行执行 SQL Skill 和 RAG Skill
Step 2: 将两者的结果合并
Step 3: Claude 综合两方面信息，生成最终答案
```

---

### 5. Result Synthesizer（结果合成器）

```python
class ResultSynthesizer:
    def synthesize(self, question: str, skill_results: list[SkillResult]) -> Response:
        # 单个数值 → 直接文本回答
        # 多行数据 → Markdown 表格
        # 趋势数据 → 建议可视化 + 文本描述
        # 文档知识 → 带引用的文本回答
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
POST   /api/datasources/upload      # 上传文件（Excel/CSV/PDF/Word/TXT/图片/音视频）
GET    /api/datasources             # 列出所有数据源
GET    /api/datasources/{id}        # 获取数据源详情（含schema/摘要/处理状态）
PUT    /api/datasources/{id}        # 更新数据源描述
DELETE /api/datasources/{id}        # 删除数据源
GET    /api/datasources/{id}/status # 查询MOI处理进度（非结构化数据）
```

### 会话管理 API
```
POST   /api/sessions                # 创建新会话
GET    /api/sessions/{id}/history   # 获取会话历史
```

---

## Tech Stack

| Component         | Technology                    | Rationale                                                |
|-------------------|-------------------------------|----------------------------------------------------------|
| Web Framework     | FastAPI                       | 异步支持、自动 API 文档、类型检查                          |
| LLM               | Claude API (anthropic SDK)    | 用户指定                                                  |
| **Database**      | **MatrixOne**                 | HTAP + 向量搜索 + 全文检索，MySQL 兼容                    |
| **数据处理平台**   | **MOI (MatrixOne Intelligence)** | 文档解析/分段/嵌入/清洗全流程 API，免自建管线            |
| DB Driver         | PyMySQL + SQLAlchemy          | MatrixOne 兼容 MySQL 协议                                 |
| Embedding         | MOI 内置 (bge-m3)            | 平台一体化，无需额外 embedding 服务                       |
| Excel Parsing     | openpyxl + pandas             | 结构化数据本地处理，简单可控                               |
| HTTP Client       | httpx                         | 异步调用 MOI API + Claude API                             |

**删掉的依赖**（被 MOI 替代）：
- ~~PyMuPDF (fitz)~~ → MOI 文档解析节点
- ~~python-docx~~ → MOI 文档解析节点
- ~~Voyage AI SDK~~ → MOI 文本嵌入节点 (bge-m3)
- ~~手写 chunking 逻辑~~ → MOI 分段节点

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
│       ├── config.py              # 配置管理（MO连接、MOI API key、Claude API key）
│       ├── api/
│       │   ├── __init__.py
│       │   ├── chat.py            # Chat 路由
│       │   ├── datasource.py      # 数据源管理路由
│       │   └── schemas.py         # Pydantic request/response models
│       ├── ingestion/
│       │   ├── __init__.py
│       │   ├── excel_loader.py    # Excel/CSV → MatrixOne 表（pandas + SQLAlchemy）
│       │   └── moi_processor.py   # PDF/Word/TXT/图片/音视频 → MOI Workflow API
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── database.py        # MatrixOne 连接池管理
│       │   └── metadata.py        # Metadata CRUD
│       ├── router/
│       │   ├── __init__.py
│       │   └── query_router.py    # 两阶段查询路由
│       ├── skills/
│       │   ├── __init__.py
│       │   ├── base.py            # Skill 基类
│       │   ├── sql_skill.py       # Text-to-SQL
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
│   └── uploads/                   # 用户上传的原始文件（临时，处理后可清理）
└── .env.example                   # 环境变量模板
```

**关键变化**：
- `ingestion/doc_loader.py` → `ingestion/moi_processor.py`（从本地解析改为调 MOI API）
- 删除了所有本地文档解析依赖

---

## MOI API Integration Detail

### MOI API 全景

MOI 提供约 80 个 API endpoint，我们主要使用以下几组：

| API 组               | 文档路径                   | Data Agent 使用场景                    |
|----------------------|---------------------------|---------------------------------------|
| 密钥管理 (Token)      | `token_api/`              | 获取 moi-key                          |
| **原子能力 API**       | `automic_api/`            | **单文件快速处理（解析+分段+嵌入）**   |
| 数据接入 (Connector)  | `data_connect_api/`       | 文件上传、批量加载                     |
| 数据处理 (Workflow)   | `data_processing_api/`    | 批量/周期性文档处理工作流              |
| 数据探索 (Explore)    | `data_explore_api_v2/`    | 查看处理结果（分块+embedding）         |
| 数据导出 (Export)     | `data_export_api/`        | 导出到 Dify 知识库等                   |

文档基础路径：`https://docs.matrixorigin.cn/zh/m1intelligence/MatrixOne-Intelligence/workflow%20api/`

### 认证

```python
# 方式1: 通过 login 获取 token 再创建 api-key
POST /auth/login
Body: {"account_name": "workspace_id", "username": "admin", "password": "xxx"}
→ Access-Token + uid

POST /user/me/api-key
Headers: {"access-token": "...", "uid": "..."}
→ moi-key

# 方式2: 直接使用已有的 moi-key（推荐，配置在 .env 中）

# 后续所有请求:
Headers: {"moi-key": "your-moi-key"}
```

### 非结构化数据处理流程

MOI 提供两种 API 路径，推荐使用原子能力 API（更简洁）：

#### 方式 A：原子能力 API（推荐，一次调用搞定）

```python
# POST /v1/genai/pipeline
# 一次调用完成：解析 → 分段 → 嵌入，无需分别创建连接器/工作流

# 远程文件（URL）
response = await moi_client.post("/v1/genai/pipeline", json={
    "file_path": "https://example.com/document.pdf",
    "steps": ["ParseNode", "ChunkNode", "EmbedNode"]
})

# 本地文件（multipart upload）
response = await moi_client.post("/v1/genai/pipeline",
    files={"file": open("document.pdf", "rb")},
    data={"steps": '["ParseNode", "ChunkNode", "EmbedNode"]'}
)
# → {"job_id": "xxx"}

# 查询处理状态
status = await moi_client.get(f"/v1/genai/jobs/{job_id}")
# → {"status": "completed", "file_id": "xxx"}

# 获取处理结果（ZIP 包含 parsed JSON/markdown/tables/images）
result = await moi_client.get(
    f"/byoa/api/v1/explore/volumes/any/files/{file_id}/raws"
)
```

**可用的处理节点**：`ParseNode`（文档解析）、`ChunkNode`（分段）、`EmbedNode`（嵌入）、`ExtractNode`（结构化提取）

#### 方式 B：Workflow API（适合批量/周期性任务）

```python
class MOIProcessor:
    """封装 MOI Workflow API 的非结构化数据处理流程"""

    async def process_document(self, file_path: str, source_name: str) -> str:
        """上传文档并创建处理工作流，返回 source_id"""

        # 1. 上传文件到 MOI
        file_id = await self._upload_file(file_path)

        # 2. 创建/复用工作流（使用 RAG 数据准备模板）
        #    节点链: 文档解析 → 分段 → 文本嵌入
        workflow_id = await self._create_or_get_workflow()

        # 3. 触发处理任务
        job_id = await self._run_workflow(workflow_id, file_id)

        # 4. 轮询等待完成
        await self._wait_for_completion(job_id)

        # 5. 获取处理结果（分块+embedding）用于元数据统计
        chunks = await self._get_processed_blocks(volume_id, file_id)

        # 6. 注册到元数据表
        source_id = await self._register_metadata(source_name, chunks)

        return source_id

    async def _upload_file(self, file_path: str) -> str:
        """POST /connectors/upload"""
        ...

    async def _create_or_get_workflow(self) -> str:
        """POST /byoa/api/v1/workflow_meta
           使用预定义的 RAG 数据准备模板"""
        ...

    async def _run_workflow(self, workflow_id: str, file_id: str) -> str:
        """触发工作流执行"""
        ...

    async def _wait_for_completion(self, job_id: str):
        """GET /byoa/api/v1/workflow_job/{job_id}/status
           轮询直到状态为 completed"""
        ...

    async def _get_processed_blocks(self, volume_id: str, file_id: str):
        """POST /byoa/api/v1/explore/volumes/{vid}/files/{fid}/blocks
           获取分块+embedding结果"""
        ...
```

### 结构化提取（额外能力）

MOI 还提供了 Quick Start API，可以直接从文档中提取结构化字段：

```python
# POST /byoa/api/v1/explore/extract
# 用 JSON Schema 定义要提取的字段，LLM 智能识别
response = await moi_client.post("/byoa/api/v1/explore/extract", json={
    "file_path": "https://example.com/resume.pdf",
    "json_schema": {
        "title": "ResumeSchema",
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "姓名"},
            "education": {"type": "string", "description": "最高学历"},
            "skills": {"type": "array", "items": {"type": "string"}, "description": "技能列表"}
        }
    }
})
# → {"results": {"name": "张三", "education": "硕士", "skills": ["Python", "SQL"]}}
```

这个能力未来可以作为第四个 Skill（Extract Skill），用于从文档中提取结构化信息。

---

## Key Design Decisions

### 1. 为什么用 MOI 替代自建非结构化处理？

| 对比项         | 自建方案                              | MOI 方案                                |
|---------------|---------------------------------------|----------------------------------------|
| PDF 解析       | PyMuPDF（纯文本提取）                  | 多模态模型（OCR + 表格 + 公式 + 层级识别）|
| 分段           | 固定 chunk_size=512, overlap=64        | 智能分段（层级感知，不跨章节）            |
| Embedding      | Voyage AI（额外 API key + 费用）       | bge-m3（平台内置）                       |
| 格式支持       | PDF + Word + TXT                       | PDF/Word/PPT/Excel/HTML/图片/音频/视频   |
| 可维护性       | 3 个解析库 + 自写分块 ≈ 500 行         | 1 个 API client ≈ 50 行                 |
| 效果迭代       | 需自行优化                              | 平台持续升级，自动获得新能力              |

### 2. 为什么结构化数据不走 MOI？
- Excel/CSV 到 SQL 表的转换非常直接，pandas 一把搞定
- 不需要复杂的解析/分段/嵌入流程
- 保持本地处理，延迟更低

### 3. 为什么路由器设计为两阶段？
- 第一阶段只看元数据摘要，快速缩小范围，节省 token
- 第二阶段看选中数据源的详细 schema，生成精准执行计划
- 避免把所有 schema 塞进一个 prompt 导致信息过载

### 4. 精准度提升策略总结
| 策略                     | 作用                                    |
|-------------------------|-----------------------------------------|
| 元数据丰富描述            | 帮助路由器精准选择数据源                  |
| 两阶段路由                | 先粗筛后精选，减少噪音                   |
| Schema + 样本数据         | 让 SQL 生成更准确                       |
| MySQL 方言               | LLM 最熟悉的 SQL 方言，准确率更高        |
| SQL Self-correction      | 执行失败自动重试修正                     |
| MOI 智能分段             | 层级感知分段，比固定 chunk 更精准         |
| 向量 + 全文双路检索       | 语义匹配 + 关键词匹配互补                |
| LLM Rerank              | 粗检索后精排，提升相关性                  |
| 结果置信度               | 低置信度时主动告知用户，避免误导           |

### 5. 安全性考虑
- SQL Skill 只生成 SELECT 语句
- MatrixOne 可配置只读用户权限
- 上传文件做类型和大小校验
- API 预留认证中间件接口
- MOI API key 加密存储

---

## Execution Flow Example

**用户问题**: "上个季度哪个产品的销售额最高？"

```
1. [Router Stage 1]
   - 查询 data_sources 表：发现有 "Q4销售报表"(structured) 和 "产品手册"(unstructured)
   - 判断：量化分析，涉及 "销售额"、"最高" → 匹配 "Q4销售报表"

2. [Router Stage 2]
   - 查询 structured_meta：列有 product, region, amount, date
   - 确定：SQL Skill，目标表 ds_q4_sales

3. [SQL Skill]
   - Claude 生成 SQL:
     SELECT `product`, SUM(`amount`) AS total
     FROM ds_q4_sales
     WHERE `date` >= '2025-10-01'
     GROUP BY `product`
     ORDER BY total DESC LIMIT 1;
   - MatrixOne 执行 → {"product": "产品A", "total": 1500000}

4. [Result Synthesizer]
   → "上个季度销售额最高的产品是**产品A**，总销售额为 150 万元。"
```

**用户问题**: "这个产品的核心卖点是什么？"（接上文）

```
1. [Router] "这个产品" = "产品A"（上下文），知识性问题 → RAG Skill
2. [RAG Skill]
   - 生成 embedding for "产品A 核心卖点"
   - MatrixOne 向量检索 + 全文检索 → 候选 chunks
   - Claude rerank → top-3
   - Claude 生成答案 + 引用
3. → "产品A 的核心卖点：1) ... 2) ... [来源: 产品手册 P.12]"
```

---

## Connection Config

```python
# .env.example

# MatrixOne Database
MO_HOST=127.0.0.1
MO_PORT=6001
MO_USER=root
MO_PASSWORD=111
MO_DATABASE=data_agent
# SQLAlchemy: mysql+pymysql://root:111@127.0.0.1:6001/data_agent

# MatrixOne Intelligence (MOI)
MOI_BASE_URL=https://freetier-01.cn-hangzhou.cluster.matrixonecloud.cn
MOI_API_KEY=your-moi-key

# Claude API
ANTHROPIC_API_KEY=your-anthropic-api-key

# Optional
LOG_LEVEL=INFO
UPLOAD_MAX_SIZE_MB=50
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

### Phase 2 - RAG 能力（MOI 集成）
- [ ] MOI API 认证 + Client 封装
- [ ] 非结构化文件上传 → MOI Workflow 处理
- [ ] MOI 处理状态轮询 + 回调
- [ ] MatrixOne 向量检索 + 全文检索
- [ ] RAG Skill（双路检索 + LLM rerank）
- [ ] 两阶段路由升级
- [ ] Hybrid Skill

### Phase 3 - 生产加固
- [ ] 会话历史与上下文记忆
- [ ] MOI 结构化提取 Skill（Extract Skill）
- [ ] 认证与权限
- [ ] 前端界面
- [ ] 监控与日志
