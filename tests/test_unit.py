"""Unit tests that don't require database connectivity."""

from data_agent.config import Settings
from data_agent.ingestion.excel_loader import _sanitize_table_name, _sanitize_column_name
from data_agent.skills.base import SkillResult
from data_agent.skills.sql_skill import _clean_sql, _is_safe


class TestSanitize:
    def test_table_name_basic(self):
        assert _sanitize_table_name("sales.xlsx") == "ds_sales"

    def test_table_name_spaces(self):
        assert _sanitize_table_name("Q4 Sales Report.csv") == "ds_q4_sales_report"

    def test_table_name_starts_with_digit(self):
        assert _sanitize_table_name("2024_data.xlsx") == "ds_t_2024_data"

    def test_column_name_basic(self):
        assert _sanitize_column_name("Product Name") == "product_name"

    def test_column_name_starts_with_digit(self):
        assert _sanitize_column_name("123col") == "c_123col"


class TestSQLSafety:
    def test_safe_select(self):
        assert _is_safe("SELECT * FROM t;")

    def test_unsafe_drop(self):
        assert not _is_safe("DROP TABLE t;")

    def test_unsafe_delete(self):
        assert not _is_safe("DELETE FROM t;")

    def test_unsafe_insert(self):
        assert not _is_safe("INSERT INTO t VALUES (1);")

    def test_unsafe_update(self):
        assert not _is_safe("UPDATE t SET x=1;")


class TestCleanSQL:
    def test_strip_markdown(self):
        raw = "```sql\nSELECT 1\n```"
        assert _clean_sql(raw) == "SELECT 1;"

    def test_already_clean(self):
        assert _clean_sql("SELECT 1;") == "SELECT 1;"

    def test_no_trailing_semicolon(self):
        assert _clean_sql("SELECT 1") == "SELECT 1;"


class TestConfig:
    def test_url_encoding(self):
        s = Settings(
            mo_user="user:with:colons",
            mo_password="pass@word",
            mo_host="localhost",
            mo_port=3306,
            mo_database="test",
        )
        url = s.mo_connection_url
        assert "user%3Awith%3Acolons" in url
        assert "pass%40word" in url
        assert "@localhost:3306/test" in url


class TestSkillResult:
    def test_defaults(self):
        r = SkillResult(skill="sql")
        assert r.answer == ""
        assert r.raw_data == []
        assert r.confidence == 0.0
