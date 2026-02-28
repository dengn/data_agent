"""Query router: decides which skill to use based on the user question."""

import json
import logging

from data_agent.llm.claude_client import chat
from data_agent.storage.metadata import get_all_metadata_summary

logger = logging.getLogger(__name__)

ROUTER_SYSTEM = """\
你是一个数据查询路由器。根据用户问题和可用数据源，选择最合适的查询策略。

## 路由规则
1. 如果问题涉及"多少"、"平均"、"总计"、"趋势"、"排名"、"对比"、"最高"、"最低"等量化分析 → 选择 "sql"
2. 如果问题涉及"什么是"、"解释"、"描述"、"为什么"、"怎么"等知识性问题 → 选择 "rag"
3. 如果问题同时需要数据查询和文档知识 → 选择 "hybrid"
4. 如果无法确定 → 默认选择 "sql"（如果有结构化数据源）

## 输出格式
严格返回 JSON（不要加 markdown 代码块），格式如下：
{
  "skill": "sql",
  "data_sources": ["source_id_1"],
  "reasoning": "选择该技能的理由",
  "sql_hint": "如果是sql，给出查询思路",
  "search_query": "如果是rag，给出检索关键词"
}
"""


def route(question: str) -> dict:
    """Route a user question to the appropriate skill.

    Returns a dict with keys: skill, data_sources, reasoning, sql_hint, search_query.
    """
    # Get all available data sources
    summaries = get_all_metadata_summary()

    if not summaries:
        return {
            "skill": "none",
            "data_sources": [],
            "reasoning": "没有可用的数据源，请先上传数据文件。",
            "sql_hint": "",
            "search_query": "",
        }

    # Build metadata context for the router
    metadata_text = json.dumps(summaries, ensure_ascii=False, indent=2, default=str)

    user_prompt = f"""\
## 可用数据源
{metadata_text}

## 用户问题
{question}
"""

    raw = chat(system=ROUTER_SYSTEM, user_message=user_prompt)

    # Parse JSON response
    try:
        # Strip potential markdown fences
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            cleaned = cleaned.rsplit("```", 1)[0]
        result = json.loads(cleaned)
    except (json.JSONDecodeError, IndexError):
        logger.warning("Router returned invalid JSON: %s", raw)
        # Fallback: use sql skill with all structured sources
        structured = [s for s in summaries if s["type"] == "structured"]
        return {
            "skill": "sql" if structured else "rag",
            "data_sources": [s["id"] for s in (structured or summaries)],
            "reasoning": "路由解析失败，使用默认策略",
            "sql_hint": "",
            "search_query": question,
        }

    return result
