"""Chat API route — the core query interface."""

import json
import logging

from fastapi import APIRouter

from data_agent.api.schemas import ChatRequest, ChatResponse
from data_agent.llm.claude_client import chat as claude_chat
from data_agent.router.query_router import route
from data_agent.skills import sql_skill, rag_skill, hybrid_skill
from data_agent.skills.base import SkillResult
from data_agent.storage.metadata import get_data_source

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])

SYNTHESIZER_SYSTEM = """\
你是一个数据分析助手。根据用户的问题和查询结果，生成清晰、准确的回答。

## 输出规则
- 如果结果是数字/统计数据，直接给出数值并简要说明
- 如果结果是多行数据，用 Markdown 表格展示
- 如果查询失败，说明原因并给出建议
- 回答要简洁，不要重复问题
- 使用中文回答
"""


def _synthesize(question: str, result: SkillResult) -> str:
    """Use Claude to turn raw skill results into a human-readable answer."""
    if result.error:
        return f"查询出错：{result.error}"

    if not result.raw_data:
        return "查询完成，但未找到匹配的结果。请检查问题描述或确认数据是否存在。"

    # For small result sets, just format directly
    data_text = json.dumps(result.raw_data[:50], ensure_ascii=False, default=str, indent=2)

    user_prompt = f"""\
## 用户问题
{question}

## 使用的查询技能
{result.skill}

## SQL 查询（如有）
{result.sql_query or "N/A"}

## 查询结果
{data_text}

请根据以上结果回答用户的问题。
"""
    return claude_chat(system=SYNTHESIZER_SYSTEM, user_message=user_prompt)


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    """Answer a natural language question over the uploaded data."""
    question = req.question.strip()
    if not question:
        return ChatResponse(answer="请输入一个问题。", error="empty question")

    # Step 1: Route
    plan = route(question)
    logger.info("Router plan: %s", plan)

    skill_name = plan.get("skill", "none")
    source_ids = plan.get("data_sources", [])

    if skill_name == "none":
        return ChatResponse(
            answer=plan.get("reasoning", "没有可用的数据源，请先上传数据文件。"),
            skill_used="none",
        )

    # Step 2: Execute skill
    if skill_name == "sql":
        result = sql_skill.execute(
            question=question,
            source_ids=source_ids,
            sql_hint=plan.get("sql_hint", ""),
        )
    elif skill_name == "rag":
        result = rag_skill.execute(
            question=question,
            source_ids=source_ids,
            search_query=plan.get("search_query", ""),
        )
    elif skill_name == "hybrid":
        result = hybrid_skill.execute(
            question=question,
            source_ids=source_ids,
            sql_hint=plan.get("sql_hint", ""),
            search_query=plan.get("search_query", ""),
        )
    else:
        result = SkillResult(skill=skill_name, error=f"未知技能: {skill_name}")

    # Step 3: Synthesize answer
    answer = _synthesize(question, result)

    # Build source info
    sources = []
    for sid in source_ids:
        ds = get_data_source(sid)
        if ds:
            sources.append({"name": ds.name, "type": ds.source_type, "id": ds.id})

    return ChatResponse(
        answer=answer,
        sources=sources,
        skill_used=result.skill,
        sql_query=result.sql_query,
        confidence=result.confidence,
        error=result.error,
    )
