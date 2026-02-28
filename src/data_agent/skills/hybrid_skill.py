"""Hybrid Skill: combines SQL + RAG for mixed queries.

Phase 2 placeholder.
"""

from data_agent.skills.base import SkillResult


def execute(question: str, source_ids: list[str], sql_hint: str = "", search_query: str = "") -> SkillResult:
    """Placeholder for Hybrid skill (Phase 2)."""
    return SkillResult(
        skill="hybrid",
        error="混合查询技能尚未实现（Phase 2）。",
        confidence=0.0,
    )
