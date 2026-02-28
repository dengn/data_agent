"""SQL Skill: text-to-SQL generation, execution, and self-correction."""

import json
import logging

from data_agent.llm.claude_client import chat
from data_agent.skills.base import SkillResult
from data_agent.storage.database import execute_query
from data_agent.storage.metadata import get_structured_meta

logger = logging.getLogger(__name__)

SQL_SYSTEM = """\
你是一个 SQL 专家。根据用户问题和表结构，生成准确的 SQL 查询。

## 注意事项
- 使用 MySQL 方言（MatrixOne 兼容 MySQL）
- 只生成 SELECT 查询（只读，不允许 INSERT/UPDATE/DELETE/DROP）
- 列名和表名用反引号包裹
- 如果涉及日期，使用标准 MySQL 日期函数
- 不要使用 LIMIT 除非用户明确要求或问题暗示只需要部分结果
- 如果问"最高"、"最低"、"第一"等，使用 ORDER BY + LIMIT 1

## 输出格式
只返回 SQL 语句，不要加任何解释或 markdown 代码块。
"""

CORRECTION_SYSTEM = """\
你之前生成的 SQL 执行出错了。请根据错误信息修正 SQL。

## 注意事项
- 仔细检查列名和表名是否正确
- 检查 SQL 语法
- 只返回修正后的 SQL 语句，不要加任何解释

## 输出格式
只返回 SQL 语句，不要加任何解释或 markdown 代码块。
"""


def _build_schema_prompt(source_ids: list[str]) -> str:
    """Build a schema description prompt for the given data sources."""
    parts = []
    for sid in source_ids:
        meta = get_structured_meta(sid)
        if meta is None:
            continue
        cols_desc = "\n".join(
            f"  - `{c['name']}` {c['type']}"
            + (f" -- 样本值: {', '.join(c.get('samples', []))}" if c.get("samples") else "")
            for c in meta.columns_json
        )
        sample_text = ""
        if meta.sample_rows:
            sample_text = "\n样本数据（前几行）:\n" + json.dumps(
                meta.sample_rows[:3], ensure_ascii=False, default=str, indent=2
            )
        parts.append(
            f"### 表 `{meta.table_name}` ({meta.row_count} 行)\n"
            f"列:\n{cols_desc}\n{sample_text}"
        )
    return "\n\n".join(parts)


def _clean_sql(raw: str) -> str:
    """Strip markdown fences and whitespace from generated SQL."""
    sql = raw.strip()
    if sql.startswith("```"):
        sql = sql.split("\n", 1)[1] if "\n" in sql else sql[3:]
        sql = sql.rsplit("```", 1)[0]
    return sql.strip().rstrip(";") + ";"


def _is_safe(sql: str) -> bool:
    """Check that the SQL is a SELECT-only query."""
    upper = sql.upper().strip()
    dangerous = ["INSERT ", "UPDATE ", "DELETE ", "DROP ", "ALTER ", "TRUNCATE ", "CREATE "]
    return upper.startswith("SELECT") and not any(kw in upper for kw in dangerous)


def execute(question: str, source_ids: list[str], sql_hint: str = "") -> SkillResult:
    """Generate and execute SQL for the given question."""
    schema_prompt = _build_schema_prompt(source_ids)

    if not schema_prompt:
        return SkillResult(
            skill="sql",
            error="未找到匹配的结构化数据源",
            confidence=0.0,
        )

    hint_text = f"\n## 查询思路提示\n{sql_hint}" if sql_hint else ""

    user_prompt = f"""\
## 表结构
{schema_prompt}
{hint_text}

## 用户问题
{question}
"""

    max_retries = 2
    last_error = ""

    for attempt in range(max_retries + 1):
        if attempt == 0:
            raw_sql = chat(system=SQL_SYSTEM, user_message=user_prompt)
        else:
            correction_prompt = f"""\
## 表结构
{schema_prompt}

## 原始问题
{question}

## 之前生成的 SQL
{sql}

## 执行错误
{last_error}
"""
            raw_sql = chat(system=CORRECTION_SYSTEM, user_message=correction_prompt)

        sql = _clean_sql(raw_sql)
        logger.info("SQL attempt %d: %s", attempt + 1, sql)

        if not _is_safe(sql):
            return SkillResult(
                skill="sql",
                sql_query=sql,
                error="生成的 SQL 包含不安全的操作，已拒绝执行。",
                confidence=0.0,
            )

        try:
            rows = execute_query(sql)
            return SkillResult(
                skill="sql",
                sql_query=sql,
                raw_data=rows,
                confidence=0.9 if rows else 0.5,
                sources=[{"id": sid} for sid in source_ids],
            )
        except Exception as e:
            last_error = str(e)
            logger.warning("SQL execution failed (attempt %d): %s", attempt + 1, last_error)

    return SkillResult(
        skill="sql",
        sql_query=sql,
        error=f"SQL 执行失败（已重试 {max_retries} 次）：{last_error}",
        confidence=0.0,
    )
