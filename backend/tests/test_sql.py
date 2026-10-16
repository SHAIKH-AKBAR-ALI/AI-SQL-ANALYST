import json
import os
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.main import app
from app.database import engine_for, user_transaction
from app.sql_executor import MAX_RESPONSE_BYTES, QueryError, compile_query, execute_query
import test_isolation as isolation_tests

DEMO = UUID('00000000-0000-0000-0000-000000000001')


class ValidationTests(unittest.TestCase):
    def test_supported_queries_are_scoped_at_every_source(self):
        cases = [
            ('SELECT sum(amount) FROM app.sales', 1),
            ('WITH x AS (SELECT * FROM sales) SELECT sum(amount) FROM x', 1),
            ('SELECT a.amount FROM sales a JOIN sales b ON a.id=b.id', 2),
            ('SELECT * FROM sales UNION ALL SELECT * FROM sales', 2),
            ('SELECT * FROM sales WHERE amount > (SELECT avg(amount) FROM sales)', 2),
            ('WITH sales AS (SELECT * FROM app.sales) SELECT * FROM sales', 1),
        ]
        for sql, sources in cases:
            with self.subTest(sql=sql):
                compiled = compile_query(sql, DEMO)
                self.assertEqual(compiled.count(f"dataset_id = CAST('{DEMO}' AS UUID)"), sources)

    def test_unsafe_queries_fail_closed_including_nested_constructs(self):
        cases = [
            '', '-- comment', 'SELECT * FROM sales; SELECT * FROM sales',
            'DELETE FROM app.sales', 'UPDATE app.sales SET amount=0',
            'COPY app.sales TO STDOUT', 'SET ROLE hisaab_import',
            'SELECT * INTO app.stolen FROM sales', 'SELECT * FROM sales FOR UPDATE',
            'WITH x AS (DELETE FROM app.sales RETURNING *) SELECT * FROM x',
            "SELECT set_config('request.jwt.claims','{}',true) FROM sales",
            "SELECT (SELECT pg_sleep(2)) FROM sales", 'SELECT pg_catalog.sum(amount) FROM sales',
            'SELECT * FROM auth.users', 'SELECT * FROM pg_catalog.pg_class',
            'SELECT * FROM app.datasets', 'SELECT * FROM other.sales',
            "SELECT * FROM sales UNION ALL SELECT * FROM auth.users",
            'SELECT * FROM generate_series(1,10000000)',
            "SELECT 'pg_authid'::regclass FROM sales", "SELECT 'x'::public.evil FROM sales",
            'SELECT amount::numeric(1000,2) FROM sales',
            "SELECT repeat(product_name,1000000) FROM sales",
            'WITH RECURSIVE x AS (SELECT * FROM sales) SELECT * FROM x',
            'SELECT * FROM sales TABLESAMPLE SYSTEM(10)',
            'SELECT current_user FROM sales', 'SELECT * FROM sales WHERE amount = :amount',
            'SELECT * FROM sales WHERE id = $1', 'SELECT 1',
            'WITH sales AS (SELECT * FROM app.sales) SELECT * FROM auth.sales',
            'WITH x AS (SELECT * FROM auth.users) SELECT * FROM sales',
            'SELECT * FROM ONLY app.sales',
            'SELECT * FROM sales WHERE id IN (SELECT id FROM auth.users)',
            "SELECT pg_read_file('/etc/passwd') FROM sales",
            "SELECT pg_advisory_lock(1) FROM sales",
            "SELECT dblink('dbname=postgres', 'SELECT 1') FROM sales",
            'SELECT * FROM sales ' + ' ' * 12000,
        ]
        for sql in cases:
            with self.subTest(sql=sql[:100]), self.assertRaises(QueryError):
                compile_query(sql, DEMO)

    def test_rejected_sql_never_opens_database(self):
        with patch('app.sql_executor.user_transaction') as transaction:
            with self.assertRaises(QueryError):
                execute_query('DROP TABLE app.sales', DEMO, None)
            transaction.assert_not_called()

    def test_api_auth_validation_and_cors(self):
        with TestClient(app) as client:
            path = f'/datasets/{DEMO}/query'
            self.assertEqual(client.post(path, json={'sql': 'SELECT * FROM sales'}).status_code, 401)
            demo = f'/demo/datasets/{DEMO}/query'
            response = client.post(demo, json={'sql': 'SELECT pg_sleep(2) FROM sales'})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn('pg_sleep', response.text)
            self.assertEqual(client.post(demo, json={'sql': 'x' * 12001}).status_code, 422)
            response = client.options(path, headers={
                'Origin': 'http://localhost:3000', 'Access-Control-Request-Method': 'POST',
                'Access-Control-Request-Headers': 'Authorization, Content-Type',
            })
            self.assertEqual(response.status_code, 200)


@unittest.skipUnless(os.getenv('PHASE2_INTEGRATION') == '1', 'Requires local Supabase; see README')
class SQLIntegrationTests(unittest.TestCase):
    # Reuse real local Auth + private data fixtures, without inheriting Phase 2 tests.
    setUpClass = classmethod(isolation_tests.IsolationTests.setUpClass.__func__)
    tearDownClass = classmethod(isolation_tests.IsolationTests.tearDownClass.__func__)

    def test_hand_checked_demo_benchmark(self):
        cases = [
            ('SELECT count(*), sum(amount) FROM sales', [[6, '2100.00']]),
            ("SELECT sum(amount) FROM sales WHERE sale_date >= '2026-09-01' AND sale_date < '2026-10-01'", [['600.00']]),
            ("SELECT sum(amount) FROM sales WHERE sale_date >= '2026-10-01' AND sale_date < '2026-11-01'", [['1440.00']]),
            ("SELECT sum(amount) FROM sales WHERE sale_date >= '2026-11-01' AND sale_date < '2026-12-01'", [['60.00']]),
            ('SELECT unit, sum(quantity) FROM sales GROUP BY unit ORDER BY unit', [['kg', '10.000'], ['litre', '27.500'], ['piece', '3.000']]),
            ('SELECT product_name, sum(amount) FROM sales GROUP BY product_name ORDER BY product_name', [['Milk', '1650.00'], ['Notebook', '150.00'], ['Potato', '300.00']]),
            ('SELECT count(unit_price), count(*) FROM sales', [[5, 6]]),
            ("SELECT sum(amount), count(*) FROM sales WHERE sale_date < '2000-01-01'", [[None, 0]]),
            ('WITH totals AS (SELECT sum(amount) AS total FROM sales) SELECT total FROM totals', [['2100.00']]),
            ('SELECT sum(amount) FROM (SELECT amount FROM sales) x', [['2100.00']]),
            ('SELECT source_row % 2 FROM sales ORDER BY source_row LIMIT 1', [[1]]),
            ("SELECT date_trunc('month', sale_date)::date, sum(amount) FROM sales GROUP BY 1 ORDER BY 1", [['2026-09-01', '600.00'], ['2026-10-01', '1440.00'], ['2026-11-01', '60.00']]),
            ('SELECT round(avg(amount), 2), coalesce(min(unit_price),0), nullif(sum(amount),0) FROM sales', [['350.00', '30.00', '2100.00']]),
            ("SELECT '; DELETE FROM app.sales; --' FROM sales LIMIT 1", [['; DELETE FROM app.sales; --']]),
            ("SELECT sale_date FROM sales WHERE product_name LIKE 'M%' ORDER BY sale_date LIMIT 1", [['2026-09-30']]),
            ("SELECT extract(month FROM sale_date), sum(amount) FROM sales GROUP BY 1 ORDER BY 1", [['9', '600.00'], ['10', '1440.00'], ['11', '60.00']]),
        ]
        for sql, expected in cases:
            with self.subTest(sql=sql):
                response = execute_query(sql, DEMO, None)
                self.assertEqual(response['rows'], expected)
                self.assertFalse(response['truncated'])

    def test_dataset_selection_is_enforced_even_without_where(self):
        for index, user in enumerate(self.users):
            self.assertEqual(execute_query('SELECT sum(amount) FROM sales', self.dataset_ids[index], user)['rows'], [['123.00']])
            self.assertEqual(execute_query('SELECT sum(amount) FROM sales', DEMO, user)['rows'], [['2100.00']])
            for other_user in (user, None):
                with self.assertRaises(QueryError) as error:
                    execute_query('SELECT * FROM sales', self.dataset_ids[1-index], other_user)
                self.assertEqual(error.exception.status, 404)
            # A false/OR predicate, joins, CTEs and nested queries cannot remove scope.
            for sql in (
                'SELECT count(*) FROM sales WHERE TRUE OR amount > 0',
                'WITH x AS (SELECT * FROM sales) SELECT count(*) FROM x',
                'SELECT count(*) FROM sales a JOIN sales b ON TRUE',
                'SELECT (SELECT count(*) FROM sales) FROM sales LIMIT 1',
            ):
                self.assertEqual(execute_query(sql, self.dataset_ids[index], user)['rows'], [[1]])

    def test_row_and_byte_limits(self):
        sql = 'SELECT a.product_name FROM sales a CROSS JOIN sales b CROSS JOIN sales c'
        result = execute_query(sql, DEMO, None)
        self.assertEqual(result['row_count'], 200)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['truncation_reason'], 'row_limit')
        sql = "SELECT '" + 'न' * 3000 + "' FROM sales a CROSS JOIN sales b"
        result = execute_query(sql, DEMO, None)
        self.assertGreater(result['row_count'], 0)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['truncation_reason'], 'response_byte_limit')
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False).encode()), MAX_RESPONSE_BYTES)
        result = execute_query('SELECT * FROM sales LIMIT 0', DEMO, None)
        self.assertEqual(result['rows'], [])
        self.assertFalse(result['truncated'])

    def test_sql_errors_are_safe_and_connection_recovers(self):
        for sql in ('SELECT amount/0 FROM sales', 'SELECT secret_password FROM sales',
                    "SELECT CAST('invalid-date' AS DATE) FROM sales"):
            with self.assertRaises(QueryError) as error:
                execute_query(sql, DEMO, None)
            self.assertEqual(error.exception.status, 400)
            self.assertNotIn('secret_password', error.exception.message)
        self.assertEqual(execute_query('SELECT count(*) FROM sales', DEMO, None)['rows'], [[6]])

    def test_transaction_controls_and_context_cleanup(self):
        @contextmanager
        def checked_transaction(user, purpose):
            with user_transaction(user, purpose) as connection:
                self.assertEqual(connection.execute(text('SHOW transaction_read_only')).scalar_one(), 'on')
                self.assertEqual(connection.execute(text('SHOW statement_timeout')).scalar_one(), '5s')
                self.assertEqual(connection.execute(text('SHOW lock_timeout')).scalar_one(), '1s')
                yield connection

        with patch('app.sql_executor.user_transaction', checked_transaction):
            execute_query('SELECT count(*) FROM sales', self.dataset_ids[0], self.users[0])
        with engine_for('query').begin() as connection:
            self.assertEqual(connection.execute(text('SELECT count(*) FROM app.sales')).scalar_one(), 0)
            self.assertNotEqual(connection.execute(text('SHOW search_path')).scalar_one(), 'pg_catalog')

    def test_expensive_query_times_out_then_recovers(self):
        with patch('app.sql_executor.monotonic', side_effect=[0, 6]):
            with self.assertRaises(QueryError) as deadline_error:
                execute_query('SELECT count(*) FROM sales', DEMO, None)
            self.assertEqual(deadline_error.exception.status, 408)
        sql = 'SELECT count(*) FROM ' + ' CROSS JOIN '.join(f'sales s{i}' for i in range(12))
        with self.assertRaises(QueryError) as error:
            execute_query(sql, DEMO, None)
        self.assertEqual(error.exception.status, 408)
        self.assertEqual(execute_query('SELECT count(*) FROM sales', DEMO, None)['rows'], [[6]])

    def test_real_query_http_path(self):
        with TestClient(app) as client:
            sql = {'sql': 'SELECT sum(amount) FROM sales'}
            response = client.post(f'/demo/datasets/{DEMO}/query', json=sql)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['rows'], [['2100.00']])
            response = client.post(f'/datasets/{self.dataset_ids[0]}/query', json=sql,
                                   headers={'Authorization': f'Bearer {self.tokens[0]}'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['rows'], [['123.00']])
            response = client.post(f'/datasets/{self.dataset_ids[1]}/query', json=sql,
                                   headers={'Authorization': f'Bearer {self.tokens[0]}'})
            self.assertEqual(response.status_code, 404)
            response = client.post(f'/demo/datasets/{DEMO}/query', json={'sql': 'SELECT amount/0 FROM sales'})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json(), {'detail': 'Query could not run; check columns, types and calculations'})
