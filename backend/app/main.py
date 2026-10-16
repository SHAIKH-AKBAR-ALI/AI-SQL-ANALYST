import os

from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse

from app.auth import Identity, current_user
from app.database import user_transaction
from app.sql_executor import QueryError, execute_query
from app.agent import QuestionRequest, ask_demo
from app.import_files import FileRequest, ValidationRequest
from app.file_jobs import preview_file, validate_file
from app.limits import UsageLimits
from app.imports import CommitRequest, commit_import
from app.history import PrivateQuestion, ask_private, read_history
from app.workflow import ask_demo_turn, run_question
from fastapi.exceptions import RequestValidationError


class BodyLimit:
    """Bound actual streamed bytes before JSON parsing, including chunked bodies."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        chunks, total = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            total += len(message.get('body', b''))
            if total > 8 * 1024 * 1024:
                return await JSONResponse({'detail': 'Request exceeds 8 MiB limit.'}, status_code=413)(scope, receive, send)
            chunks.append(message)
            if not message.get('more_body', False):
                break
        async def replay():
            return chunks.pop(0) if chunks else await receive()
        await self.app(scope, replay, send)

app = FastAPI(title="HisaabAI Agent API", version="0.1.0")
app.add_middleware(BodyLimit)
app.add_middleware(UsageLimits)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv(
        "CORS_ORIGINS", "http://127.0.0.1:3000,http://localhost:3000"
    ).split(",") if origin.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Accept", "Content-Type", "Authorization"],
)


@app.get("/health")
def health() -> dict[str, str]:
    """Process liveness only; database readiness belongs to a later phase."""
    return {"status": "ok", "service": "hisaabai-api"}


@app.get("/me")
async def me(user: Identity = Depends(current_user)):
    return {"id": str(user.id), "email": user.email}


def read_datasets(user: Identity | None, dataset_id: UUID | None = None):
    try:
        with user_transaction(user, "query" if user else "demo") as connection:
            sql = """SELECT d.id, d.name, d.is_demo, d.currency, d.business_timezone, d.version,
                (SELECT count(*) FROM app.sales s WHERE s.dataset_id=d.id) AS row_count,
                (SELECT min(sale_date) FROM app.sales s WHERE s.dataset_id=d.id) AS date_start,
                (SELECT max(sale_date) FROM app.sales s WHERE s.dataset_id=d.id) AS date_end
                FROM app.datasets d"""
            params = {}
            if dataset_id is not None:
                sql += " WHERE id = :id"
                params["id"] = dataset_id
            return [dict(row) for row in connection.execute(text(sql + " ORDER BY name, id"), params).mappings()]
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(503, "Data temporarily unavailable") from exc


@app.get("/datasets")
def datasets(user: Identity = Depends(current_user)):
    return {"datasets": read_datasets(user)}


@app.get("/datasets/{dataset_id}")
def dataset(dataset_id: UUID, user: Identity = Depends(current_user)):
    rows = read_datasets(user, dataset_id)
    if not rows:
        raise HTTPException(404, "Dataset not found")
    return rows[0]


@app.get("/demo/datasets")
def demo_datasets():
    return {"datasets": read_datasets(None)}


class SQLRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=12000)


@app.exception_handler(QueryError)
async def query_error_handler(request, exc: QueryError):
    content = {'detail': exc.message}
    telemetry = getattr(exc, 'telemetry', None)
    if telemetry is not None:
        content['trace_id'] = telemetry['trace_id']
    return JSONResponse(status_code=exc.status, content=content)


@app.post('/datasets/{dataset_id}/query')
def query_dataset(dataset_id: UUID, body: SQLRequest, user: Identity = Depends(current_user)):
    return execute_query(body.sql, dataset_id, user)


@app.post('/demo/datasets/{dataset_id}/query')
def query_demo(dataset_id: UUID, body: SQLRequest):
    return execute_query(body.sql, dataset_id, None)


@app.post('/demo/datasets/{dataset_id}/ask')
async def ask_demo_dataset(dataset_id: UUID, body: QuestionRequest, request: Request):
    return await run_question(request, ask_demo_turn, body, dataset_id)


@app.exception_handler(RequestValidationError)
async def invalid_request(request, exc):
    # Do not echo uploaded bytes, question text or credentials in validation errors.
    return JSONResponse(status_code=422, content={'detail': 'Invalid request fields. Check required values and limits.'})


@app.post('/imports/preview')
def import_preview(body: FileRequest, user: Identity = Depends(current_user)):
    return preview_file(body)


@app.post('/imports/validate')
def import_validate(body: ValidationRequest, user: Identity = Depends(current_user)):
    return validate_file(body)[0]


@app.post('/imports/commit')
def import_commit(body: CommitRequest, user: Identity = Depends(current_user)):
    return commit_import(body, user)


@app.post('/datasets/{dataset_id}/ask')
async def ask_private_dataset(dataset_id: UUID, body: PrivateQuestion, request: Request, user: Identity = Depends(current_user)):
    return await run_question(request, ask_private, body, dataset_id, user)


@app.get('/datasets/{dataset_id}/history')
def dataset_history(dataset_id: UUID, user: Identity = Depends(current_user)):
    return read_history(dataset_id, user)


@app.get('/datasets/{dataset_id}/imports')
def dataset_imports(dataset_id: UUID, user: Identity = Depends(current_user)):
    try:
        with user_transaction(user) as connection:
            if not connection.execute(text('SELECT id FROM app.datasets WHERE id=:id AND owner_id=:owner'),
                                      {'id': dataset_id, 'owner': user.id}).first():
                raise HTTPException(404, 'Dataset not found')
            rows = connection.execute(text('''SELECT id, dataset_version, status, accepted_rows,
                rejected_rows, created_at FROM app.imports WHERE dataset_id=:id
                ORDER BY dataset_version DESC LIMIT 100'''), {'id': dataset_id}).mappings()
            return {'imports': [dict(row) for row in rows]}
    except (RuntimeError, SQLAlchemyError) as exc:
        raise HTTPException(503, 'Import history temporarily unavailable') from exc
