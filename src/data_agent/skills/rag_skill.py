"""RAG Skill: vector + fulltext search over unstructured data.

Phase 2 placeholder — will use MOI embedding + MatrixOne vector search.
"""

from data_agent.skills.base import SkillResult


def execute(question: str, source_ids: list[str], search_query: str = "") -> SkillResult:
    """Placeholder for RAG skill (Phase 2)."""
    return SkillResult(
        skill="rag",
        error="RAG 技能尚未实现（Phase 2），请先使用结构化数据查询。",
        confidence=0.0,
    )
