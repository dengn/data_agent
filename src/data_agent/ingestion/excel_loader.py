"""Load Excel/CSV files into MatrixOne tables."""

import logging
import re
from pathlib import Path

import pandas as pd
from sqlalchemy import text

from data_agent.storage.database import get_connection
from data_agent.storage.metadata import create_data_source, save_structured_meta

logger = logging.getLogger(__name__)

# pandas dtype → MySQL type mapping
_TYPE_MAP = {
    "int64": "BIGINT",
    "int32": "INT",
    "float64": "DOUBLE",
    "float32": "FLOAT",
    "bool": "BOOLEAN",
    "datetime64[ns]": "DATETIME",
    "object": "TEXT",
}


def _sanitize_table_name(name: str) -> str:
    """Convert filename to a valid table name."""
    name = Path(name).stem
    name = re.sub(r"[^\w]", "_", name)
    name = re.sub(r"_+", "_", name).strip("_").lower()
    if not name or name[0].isdigit():
        name = "t_" + name
    return "ds_" + name[:60]


def _sanitize_column_name(col: str) -> str:
    """Sanitize a column name."""
    col = re.sub(r"[^\w]", "_", str(col))
    col = re.sub(r"_+", "_", col).strip("_").lower()
    if not col or col[0].isdigit():
        col = "c_" + col
    return col[:64]


def _infer_mysql_type(series: pd.Series) -> str:
    """Map a pandas Series dtype to MySQL type."""
    dtype = str(series.dtype)
    if dtype in _TYPE_MAP:
        return _TYPE_MAP[dtype]
    # Try to detect dates stored as strings
    if dtype == "object":
        sample = series.dropna().head(20)
        if len(sample) > 0:
            try:
                pd.to_datetime(sample)
                return "DATETIME"
            except (ValueError, TypeError):
                pass
    return "TEXT"


def load_excel(file_path: str, file_name: str) -> str:
    """Load an Excel file into MatrixOne. Returns the data source id."""
    df = pd.read_excel(file_path, engine="openpyxl")
    return _load_dataframe(df, file_name, "excel")


def load_csv(file_path: str, file_name: str) -> str:
    """Load a CSV file into MatrixOne. Returns the data source id."""
    # Try to detect encoding and delimiter
    df = pd.read_csv(file_path)
    return _load_dataframe(df, file_name, "csv")


def _load_dataframe(df: pd.DataFrame, file_name: str, source_type: str) -> str:
    """Common logic to load a DataFrame into MatrixOne."""
    if df.empty:
        raise ValueError(f"File '{file_name}' is empty")

    table_name = _sanitize_table_name(file_name)

    # Sanitize column names
    original_cols = list(df.columns)
    sanitized_cols = [_sanitize_column_name(c) for c in original_cols]
    # Handle duplicate column names
    seen = {}
    for i, col in enumerate(sanitized_cols):
        if col in seen:
            seen[col] += 1
            sanitized_cols[i] = f"{col}_{seen[col]}"
        else:
            seen[col] = 0
    df.columns = sanitized_cols

    # Build column types
    col_types = []
    for col in sanitized_cols:
        mysql_type = _infer_mysql_type(df[col])
        col_types.append({"name": col, "type": mysql_type})

    # Create table in MatrixOne
    col_defs = ", ".join(
        f"`{c['name']}` {c['type']}" for c in col_types
    )
    create_sql = f"CREATE TABLE IF NOT EXISTS `{table_name}` ({col_defs})"

    with get_connection() as conn:
        # Drop if exists to allow re-upload
        conn.execute(text(f"DROP TABLE IF EXISTS `{table_name}`"))
        conn.execute(text(create_sql))

        # Batch insert using parameterized queries
        if len(df) > 0:
            placeholders = ", ".join([f":{c}" for c in sanitized_cols])
            insert_sql = (
                f"INSERT INTO `{table_name}` "
                f"({', '.join(f'`{c}`' for c in sanitized_cols)}) "
                f"VALUES ({placeholders})"
            )
            # Insert in batches of 500
            batch_size = 500
            df_clean = df.where(pd.notnull(df), None)
            for start in range(0, len(df_clean), batch_size):
                batch = df_clean.iloc[start:start + batch_size]
                rows = batch.to_dict("records")
                conn.execute(text(insert_sql), rows)

    row_count = len(df)

    # Collect column metadata with sample values
    columns_meta = []
    for ct in col_types:
        col_name = ct["name"]
        samples = df[col_name].dropna().head(3).tolist()
        columns_meta.append({
            "name": col_name,
            "type": ct["type"],
            "description": "",
            "samples": [str(s) for s in samples],
        })

    # Collect sample rows (first 5)
    sample_rows = df.head(5).where(pd.notnull(df.head(5)), None).to_dict("records")

    # Register in metadata
    source_id = create_data_source(
        name=file_name,
        ds_type="structured",
        source_type=source_type,
    )
    save_structured_meta(
        source_id=source_id,
        table_name=table_name,
        columns=columns_meta,
        row_count=row_count,
        sample_rows=sample_rows,
    )

    logger.info(
        "Loaded %s → table '%s' (%d rows, %d cols)",
        file_name, table_name, row_count, len(col_types),
    )
    return source_id
