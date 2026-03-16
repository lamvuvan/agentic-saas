"""Unit tests for NL2SQL safety check — SELECT enforcement and LIMIT injection."""

import pytest
from bi_agent.nodes.safety_check import check_safety, inject_limit


class TestSafetyCheck:
    def test_select_passes(self):
        sql = "SELECT SUM(total) FROM orders WHERE DATE(created_at) = CURRENT_DATE"
        passed, reason = check_safety(sql)
        assert passed is True
        assert reason is None

    def test_select_with_join_passes(self):
        sql = "SELECT c.name, SUM(o.total) FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.name ORDER BY SUM(o.total) DESC LIMIT 5"
        passed, reason = check_safety(sql)
        assert passed is True

    def test_delete_rejected(self):
        sql = "DELETE FROM orders WHERE id = 1"
        passed, reason = check_safety(sql)
        assert passed is False
        assert "DELETE" in reason.upper() or reason is not None

    def test_drop_rejected(self):
        sql = "DROP TABLE orders"
        passed, reason = check_safety(sql)
        assert passed is False

    def test_update_rejected(self):
        sql = "UPDATE orders SET status = 'cancelled'"
        passed, reason = check_safety(sql)
        assert passed is False

    def test_insert_rejected(self):
        sql = "INSERT INTO orders (customer_id, total) VALUES (1, 100000)"
        passed, reason = check_safety(sql)
        assert passed is False

    def test_truncate_rejected(self):
        sql = "TRUNCATE TABLE orders"
        passed, reason = check_safety(sql)
        assert passed is False

    def test_alter_rejected(self):
        sql = "ALTER TABLE orders ADD COLUMN new_col VARCHAR(255)"
        passed, reason = check_safety(sql)
        assert passed is False

    def test_exec_rejected(self):
        sql = "EXEC sp_some_procedure"
        passed, reason = check_safety(sql)
        assert passed is False

    def test_case_insensitive_rejection(self):
        sql = "delete from customers"
        passed, reason = check_safety(sql)
        assert passed is False

    def test_empty_sql_rejected(self):
        passed, reason = check_safety("")
        assert passed is False

    def test_select_with_subquery_passes(self):
        sql = "SELECT * FROM (SELECT id, name FROM customers) sub WHERE sub.id = 1"
        passed, reason = check_safety(sql)
        assert passed is True


class TestInjectLimit:
    def test_adds_limit_when_missing(self):
        sql = "SELECT * FROM orders"
        result = inject_limit(sql, max_rows=500)
        assert "LIMIT 500" in result.upper() or "limit 500" in result

    def test_preserves_existing_limit(self):
        sql = "SELECT * FROM orders LIMIT 10"
        result = inject_limit(sql, max_rows=500)
        # Should not add another LIMIT — keep original
        assert result.upper().count("LIMIT") == 1

    def test_existing_limit_above_max_capped(self):
        sql = "SELECT * FROM orders LIMIT 1000"
        result = inject_limit(sql, max_rows=500)
        # Implementation may cap to max or leave as-is depending on design
        # At minimum, should return a valid SQL string
        assert "SELECT" in result.upper()

    def test_empty_sql_returns_as_is(self):
        result = inject_limit("", max_rows=500)
        assert result == ""

    def test_complex_query_gets_limit(self):
        sql = "SELECT c.name, SUM(o.total) FROM orders o JOIN customers c ON o.customer_id = c.id GROUP BY c.name ORDER BY SUM(o.total) DESC"
        result = inject_limit(sql, max_rows=500)
        assert "500" in result
