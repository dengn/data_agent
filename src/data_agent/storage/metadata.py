"""Metadata CRUD operations for data sources."""

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text

from data_agent.storage.database import get_connection

logger = logging.getLogger(__name__)


@dataclass
class DataSource:
    id: str
    name: str
    type: str  # "structured" | "unstructured"
    source_type: str  # "excel" | "csv" | "pdf" | "word" | "txt"
    description: str | None = None
    moi_volume_id: str | None = None
    moi_workflow_id: str | None = None
    created_at: datetime | None = None


@dataclass
class StructuredMeta:
    source_id: str
    table_name: str
    columns_json: list[dict]  # [{name, type, description, samples}]
    row_count: int
    sample_rows: list[dict]  # first 5 rows


@dataclass
class UnstructuredMeta:
    source_id: str
    chunk_count: int
    doc_summary: str | None = None


def create_data_source(
    name: str,
    ds_type: str,
    source_type: str,
    description: str | None = None,
) -> str:
    """Create a new data source entry. Returns its id."""
    source_id = str(uuid.uuid4())
    with get_connection() as conn:
        conn.execute(
            text(
                "INSERT INTO data_sources (id, name, type, source_type, description) "
                "VALUES (:id, :name, :type, :source_type, :desc)"
            ),
            {"id": source_id, "name": name, "type": ds_type,
             "source_type": source_type, "desc": description},
        )
    return source_id


def save_structured_meta(
    source_id: str,
    table_name: str,
    columns: list[dict],
    row_count: int,
    sample_rows: list[dict],
) -> None:
    """Save schema metadata for a structured data source."""
    with get_connection() as conn:
        conn.execute(
            text(
                "INSERT INTO structured_meta "
                "(source_id, table_name, columns_json, row_count, sample_rows) "
                "VALUES (:sid, :tn, :cj, :rc, :sr)"
            ),
            {
                "sid": source_id,
                "tn": table_name,
                "cj": json.dumps(columns, ensure_ascii=False),
                "rc": row_count,
                "sr": json.dumps(sample_rows, ensure_ascii=False, default=str),
            },
        )


def get_data_source(source_id: str) -> DataSource | None:
    """Retrieve a single data source by id."""
    with get_connection() as conn:
        row = conn.execute(
            text("SELECT * FROM data_sources WHERE id = :id"), {"id": source_id}
        ).fetchone()
    if row is None:
        return None
    return DataSource(**dict(zip(row._fields, row)))


def list_data_sources() -> list[DataSource]:
    """List all data sources."""
    with get_connection() as conn:
        rows = conn.execute(
            text("SELECT * FROM data_sources ORDER BY created_at DESC")
        ).fetchall()
    return [DataSource(**dict(zip(r._fields, r))) for r in rows]


def get_structured_meta(source_id: str) -> StructuredMeta | None:
    """Get structured metadata for a data source."""
    with get_connection() as conn:
        row = conn.execute(
            text("SELECT * FROM structured_meta WHERE source_id = :sid"),
            {"sid": source_id},
        ).fetchone()
    if row is None:
        return None
    d = dict(zip(row._fields, row))
    d["columns_json"] = json.loads(d["columns_json"]) if isinstance(d["columns_json"], str) else d["columns_json"]
    d["sample_rows"] = json.loads(d["sample_rows"]) if isinstance(d["sample_rows"], str) else d["sample_rows"]
    return StructuredMeta(**d)


def update_data_source_description(source_id: str, description: str) -> None:
    """Update the description of a data source."""
    with get_connection() as conn:
        conn.execute(
            text("UPDATE data_sources SET description = :desc WHERE id = :id"),
            {"desc": description, "id": source_id},
        )


def delete_data_source(source_id: str) -> str | None:
    """Delete a data source and its metadata. Returns table_name if structured."""
    table_name = None
    with get_connection() as conn:
        # Check if structured, get table name for cleanup
        row = conn.execute(
            text("SELECT table_name FROM structured_meta WHERE source_id = :sid"),
            {"sid": source_id},
        ).fetchone()
        if row:
            table_name = row[0]
            conn.execute(
                text("DELETE FROM structured_meta WHERE source_id = :sid"),
                {"sid": source_id},
            )
        else:
            conn.execute(
                text("DELETE FROM unstructured_meta WHERE source_id = :sid"),
                {"sid": source_id},
            )
        conn.execute(
            text("DELETE FROM data_sources WHERE id = :id"), {"id": source_id}
        )
    return table_name


def get_all_metadata_summary() -> list[dict]:
    """Get a summary of all data sources with their metadata for the router.

    Returns a list of dicts with keys: id, name, type, source_type, description,
    and either columns_info (structured) or doc_summary (unstructured).
    """
    sources = list_data_sources()
    summaries = []
    for src in sources:
        entry = {
            "id": src.id,
            "name": src.name,
            "type": src.type,
            "source_type": src.source_type,
            "description": src.description or "",
        }
        if src.type == "structured":
            meta = get_structured_meta(src.id)
            if meta:
                entry["table_name"] = meta.table_name
                entry["row_count"] = meta.row_count
                # Summarize columns
                cols = meta.columns_json
                entry["columns"] = [
                    {"name": c["name"], "type": c["type"]} for c in cols
                ]
                entry["sample_rows"] = meta.sample_rows[:3]
        else:
            # unstructured - will be filled in Phase 2
            entry["doc_summary"] = ""
        summaries.append(entry)
    return summaries
