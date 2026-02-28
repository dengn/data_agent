"""Base class for query skills."""

from dataclasses import dataclass, field


@dataclass
class SkillResult:
    skill: str  # "sql" | "rag" | "hybrid"
    answer: str = ""
    raw_data: list[dict] = field(default_factory=list)
    sql_query: str = ""
    sources: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    error: str = ""
