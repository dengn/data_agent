"""Data source management API routes."""

import logging
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, UploadFile, File

from data_agent.api.schemas import DataSourceDetail, DataSourceInfo, UploadResponse
from data_agent.config import settings
from data_agent.ingestion.excel_loader import load_csv, load_excel
from data_agent.storage.database import get_connection
from data_agent.storage.metadata import (
    delete_data_source,
    get_data_source,
    get_structured_meta,
    list_data_sources,
    update_data_source_description,
)
from sqlalchemy import text

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/datasources", tags=["datasources"])

ALLOWED_EXTENSIONS = {
    "structured": {".xlsx", ".xls", ".csv"},
    "unstructured": {".pdf", ".docx", ".txt", ".md"},
}


@router.post("/upload", response_model=UploadResponse)
async def upload_file(file: UploadFile = File(...)):
    """Upload a data file (Excel, CSV, PDF, Word, TXT)."""
    if not file.filename:
        raise HTTPException(400, "No filename provided")

    ext = Path(file.filename).suffix.lower()
    all_allowed = ALLOWED_EXTENSIONS["structured"] | ALLOWED_EXTENSIONS["unstructured"]
    if ext not in all_allowed:
        raise HTTPException(400, f"Unsupported file type: {ext}. Allowed: {all_allowed}")

    # Save to upload dir
    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    file_path = upload_dir / file.filename
    with open(file_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    try:
        if ext in ALLOWED_EXTENSIONS["structured"]:
            if ext == ".csv":
                source_id = load_csv(str(file_path), file.filename)
            else:
                source_id = load_excel(str(file_path), file.filename)

            ds = get_data_source(source_id)
            meta = get_structured_meta(source_id)
            return UploadResponse(
                source_id=source_id,
                name=ds.name,
                type="structured",
                table_name=meta.table_name if meta else None,
                row_count=meta.row_count if meta else None,
                message=f"Successfully loaded {meta.row_count} rows into table '{meta.table_name}'"
                if meta else "Loaded successfully",
            )
        else:
            # Phase 2: MOI processing
            raise HTTPException(
                501,
                f"Unstructured file processing ({ext}) is not yet implemented (Phase 2).",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to process uploaded file: %s", file.filename)
        raise HTTPException(500, f"Failed to process file: {e}")
    finally:
        # Clean up uploaded file
        if file_path.exists():
            os.remove(file_path)


@router.get("", response_model=list[DataSourceInfo])
async def list_sources():
    """List all data sources."""
    sources = list_data_sources()
    return [
        DataSourceInfo(
            id=s.id,
            name=s.name,
            type=s.type,
            source_type=s.source_type,
            description=s.description,
            created_at=str(s.created_at) if s.created_at else None,
        )
        for s in sources
    ]


@router.get("/{source_id}", response_model=DataSourceDetail)
async def get_source_detail(source_id: str):
    """Get detailed info about a data source."""
    ds = get_data_source(source_id)
    if ds is None:
        raise HTTPException(404, "Data source not found")

    detail = DataSourceDetail(
        id=ds.id,
        name=ds.name,
        type=ds.type,
        source_type=ds.source_type,
        description=ds.description,
        created_at=str(ds.created_at) if ds.created_at else None,
    )

    if ds.type == "structured":
        meta = get_structured_meta(ds.id)
        if meta:
            detail.table_name = meta.table_name
            detail.columns = meta.columns_json
            detail.row_count = meta.row_count
            detail.sample_rows = meta.sample_rows

    return detail


@router.put("/{source_id}")
async def update_source(source_id: str, description: str):
    """Update the description of a data source."""
    ds = get_data_source(source_id)
    if ds is None:
        raise HTTPException(404, "Data source not found")
    update_data_source_description(source_id, description)
    return {"message": "Updated"}


@router.delete("/{source_id}")
async def delete_source(source_id: str):
    """Delete a data source and its associated table/vectors."""
    ds = get_data_source(source_id)
    if ds is None:
        raise HTTPException(404, "Data source not found")

    table_name = delete_data_source(source_id)
    if table_name:
        with get_connection() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS `{table_name}`"))

    return {"message": f"Deleted data source '{ds.name}'"}
