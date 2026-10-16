"""Fail-closed analytical SQL subset. Every physical sales source is dataset scoped."""
import json
import math
from datetime import date, datetime
from decimal import Decimal
from time import monotonic
from uuid import UUID

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import traverse_scope
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.auth import Identity
from app.database import user_transaction
from app.telemetry import measured

MAX_SQL_BYTES = 12_000
MAX_ROWS = 200
MAX_RESPONSE_BYTES = 65_536
EXECUTION_SECONDS = 5
COLUMNS = 'id dataset_id source_row sale_date product_name quantity unit unit_price amount'.split()
# Exact classes, never base-class membership: new parser constructs fail closed.
ALLOWED = set('''Select From Table Identifier TableAlias Column Star Alias Literal
    Where Group Having Order Ordered Limit Offset Distinct Join Subquery With CTE
    Union Intersect Except Paren Tuple And Or Not EQ NEQ GT GTE LT LTE Is Null
    Boolean Between In Like ILike Add Sub Mul Div Mod Neg Case If Cast DataType
    Sum Count Avg Min Max Round Abs Coalesce Nullif Extract TimestampTrunc Var'''.split())
CAST_TYPES = {'DATE', 'TIMESTAMP', 'TIMESTAMPTZ', 'DECIMAL', 'INT', 'BIGINT', 'TEXT', 'BOOLEAN'}


class QueryError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


@measured('sql_validation')
def compile_query(sql: str, dataset_id: UUID) -> str:
    """Validate whole AST, then generate SQL; original text is never executed."""
    dataset_id = UUID(str(dataset_id))
    if not sql.strip() or len(sql.encode('utf-8')) > MAX_SQL_BYTES:
        raise QueryError(400, 'SQL must contain 1 to 12000 UTF-8 bytes')
    try:
        statements = sqlglot.parse(sql, read='postgres')
        if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.SetOperation)):
            raise QueryError(400, 'Only one analytical SELECT is allowed')
        tree = statements[0]
        nodes = list(tree.walk())
        if len(nodes) > 600:
            raise QueryError(400, 'SQL is too complex')
        for node in nodes:
            if type(node).__name__ not in ALLOWED:
                raise QueryError(400, 'SQL construct or function is not supported')
            if isinstance(node, exp.With) and node.args.get('recursive'):
                raise QueryError(400, 'Recursive queries are not supported')
            if isinstance(node, exp.Column) and (node.db or node.catalog):
                raise QueryError(400, 'Schema-qualified columns are not supported; use a table alias')
            if isinstance(node, exp.DataType) and (
                node.this.value not in CAST_TYPES or node.expressions or node.args.get('nested')
            ):
                raise QueryError(400, 'Cast type is not supported')
            # Var is only permitted for parser-normalized date parts, never raw SQL.
            if isinstance(node, exp.Var) and (
                not isinstance(node.parent, (exp.Extract, exp.TimestampTrunc))
                or node.name.upper() not in {'YEAR', 'QUARTER', 'MONTH', 'WEEK', 'DAY', 'DOW', 'DOY'}
            ):
                raise QueryError(400, 'Date part is not supported')
        physical = {}
        for scope in traverse_scope(tree):
            for _, source in scope.selected_sources.values():
                if isinstance(source, exp.Table):
                    if (source.name != 'sales' or source.db not in ('', 'app') or source.catalog
                            or not isinstance(source.this, exp.Identifier)
                            or any(value for key, value in source.args.items() if key not in {'this', 'db', 'alias'})):
                        raise QueryError(400, 'Only sales or app.sales may be queried')
                    physical[id(source)] = source
        if not physical:
            raise QueryError(400, 'Query must read sales')
        for source in physical.values():
            scoped = exp.select(*COLUMNS).from_('app.sales').where(
                exp.column('dataset_id').eq(exp.cast(exp.Literal.string(str(dataset_id)), 'UUID'))
            ).subquery(source.alias_or_name)
            # Preserve aliases and optional alias-column lists.
            if source.args.get('alias'):
                scoped.set('alias', source.args['alias'].copy())
            source.replace(scoped)
        return tree.sql(dialect='postgres', comments=False, unsupported_level=sqlglot.ErrorLevel.RAISE)
    except (SqlglotError, RecursionError, ValueError, TypeError) as exc:
        raise QueryError(400, 'SQL could not be validated') from exc


def json_value(value):
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value


@measured('sql_execution')
def execute_query(sql: str, dataset_id: UUID, user: Identity | None) -> dict:
    compiled = compile_query(sql, dataset_id)
    try:
        with user_transaction(user, 'query' if user else 'demo') as connection:
            dataset = connection.execute(text(
                'SELECT version, currency, business_timezone FROM app.datasets WHERE id=:id'
            ), {'id': dataset_id}).mappings().one_or_none()
            if dataset is None:
                raise QueryError(404, 'Dataset not found')
            # Built-in resolution only; user functions cannot shadow approved names.
            connection.execute(text('SET LOCAL search_path = pg_catalog'))
            connection.execute(text("SET LOCAL work_mem = '4MB'"))
            deadline = monotonic() + EXECUTION_SECONDS
            # Server cursor prevents client buffering an unbounded result. Wrapper caps fetches.
            # no_parameters preserves literal percent signs without psycopg placeholder parsing.
            with connection.execution_options(stream_results=True, max_row_buffer=1, no_parameters=True).exec_driver_sql(
                f'SELECT * FROM ({compiled}) AS bounded_result LIMIT {MAX_ROWS + 1}'
            ) as result:
                payload = {
                    'dataset_id': str(dataset_id), 'dataset_version': dataset['version'],
                    'currency': dataset['currency'], 'business_timezone': dataset['business_timezone'],
                    'columns': list(result.keys()), 'rows': [], 'row_count': 0,
                    'truncated': False, 'truncation_reason': None,
                    'row_limit': MAX_ROWS, 'response_byte_limit': MAX_RESPONSE_BYTES,
                }
                for row in result:
                    if monotonic() > deadline:
                        raise QueryError(408, 'Query exceeded its execution time limit')
                    if len(payload['rows']) == MAX_ROWS:
                        payload.update(truncated=True, truncation_reason='row_limit')
                        break
                    payload['rows'].append([json_value(value) for value in row])
                    payload['row_count'] = len(payload['rows'])
                    # Reserve metadata space for truncation notice.
                    if len(json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_RESPONSE_BYTES - 128:
                        payload['rows'].pop()
                        payload['row_count'] = len(payload['rows'])
                        payload.update(truncated=True, truncation_reason='response_byte_limit')
                        break
                return payload
    except DBAPIError as exc:
        code = getattr(exc.orig, 'sqlstate', '') or ''
        if code in {'57014', '55P03'}:
            raise QueryError(408, 'Query exceeded its execution time limit') from exc
        if code.startswith(('22', '42')):
            raise QueryError(400, 'Query could not run; check columns, types and calculations') from exc
        raise QueryError(503, 'Data temporarily unavailable') from exc
    except (RuntimeError, SQLAlchemyError) as exc:
        raise QueryError(503, 'Data temporarily unavailable') from exc
