"""MatrixOne database connection and schema initialization."""

import logging
from contextlib import contextmanager
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

from data_agent.config import settings

logger = logging.getLogger(__name__)

_engine = None

INIT_SQLS = [
    # data_sources: registry of all uploaded data sources
    """
    CREATE TABLE IF NOT EXISTS data_sources (
        id VARCHAR(36) PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        type VARCHAR(20) NOT NULL,
        source_type VARCHAR(20) NOT NULL,
        description TEXT,
        moi_volume_id VARCHAR(64),
        moi_workflow_id VARCHAR(64),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    # structured_meta: schema info for Excel/CSV tables
    """
    CREATE TABLE IF NOT EXISTS structured_meta (
        source_id VARCHAR(36) PRIMARY KEY,
        table_name VARCHAR(255),
        columns_json TEXT,
        row_count INT,
        sample_rows TEXT
    )
    """,
    # unstructured_meta: info for document sources
    """
    CREATE TABLE IF NOT EXISTS unstructured_meta (
        source_id VARCHAR(36) PRIMARY KEY,
        chunk_count INT,
        doc_summary TEXT
    )
    """,
]


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            settings.mo_connection_url,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_recycle=3600,
            pool_pre_ping=True,
        )
    return _engine


@contextmanager
def get_connection():
    """Get a database connection from the pool."""
    engine = get_engine()
    conn = engine.connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_database():
    """Create the data_agent database and metadata tables."""
    # First connect without database to create it
    user = quote_plus(settings.mo_user)
    password = quote_plus(settings.mo_password)
    base_url = (
        f"mysql+pymysql://{user}:{password}"
        f"@{settings.mo_host}:{settings.mo_port}/"
    )
    base_engine = create_engine(base_url)
    with base_engine.connect() as conn:
        conn.execute(text(f"CREATE DATABASE IF NOT EXISTS `{settings.mo_database}`"))
        conn.commit()
    base_engine.dispose()

    # Now create tables in the target database
    with get_connection() as conn:
        for sql in INIT_SQLS:
            conn.execute(text(sql))
    logger.info("Database initialized: %s", settings.mo_database)


def execute_query(sql: str, params: dict | None = None) -> list[dict]:
    """Execute a read query and return rows as dicts."""
    with get_connection() as conn:
        result = conn.execute(text(sql), params or {})
        columns = list(result.keys())
        return [dict(zip(columns, row)) for row in result.fetchall()]


def execute_write(sql: str, params: dict | None = None) -> int:
    """Execute a write query and return affected rows."""
    with get_connection() as conn:
        result = conn.execute(text(sql), params or {})
        return result.rowcount
