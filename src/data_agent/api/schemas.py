"""Pydantic models for API request/response."""

from pydantic import BaseModel


class ChatRequest(BaseModel):
    question: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    answer: str
    sources: list[dict] = []
    skill_used: str = ""
    sql_query: str = ""
    confidence: float = 0.0
    error: str = ""


class DataSourceInfo(BaseModel):
    id: str
    name: str
    type: str
    source_type: str
    description: str | None = None
    created_at: str | None = None


class DataSourceDetail(DataSourceInfo):
    table_name: str | None = None
    columns: list[dict] | None = None
    row_count: int | None = None
    sample_rows: list[dict] | None = None
    doc_summary: str | None = None
    chunk_count: int | None = None


class UploadResponse(BaseModel):
    source_id: str
    name: str
    type: str
    table_name: str | None = None
    row_count: int | None = None
    message: str = ""
