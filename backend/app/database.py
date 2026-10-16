"""Only trusted backend code sets identity; untrusted SQL must use sql_executor."""
import json
import os
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, text

from app.auth import Identity

ROLES = {"query": "hisaab_query", "import": "hisaab_import", "demo": "hisaab_demo"}


@lru_cache(maxsize=3)
def engine_for(purpose: str):
    url = os.getenv(f"{purpose.upper()}_DATABASE_URL", "")
    if not url:
        raise RuntimeError("Database is not configured")
    return create_engine(url, pool_size=3, max_overflow=0, pool_pre_ping=True, hide_parameters=True,
                         connect_args={"connect_timeout": 5})


@contextmanager
def user_transaction(identity: Identity | None, purpose: str = "query"):
    if purpose not in ROLES or (purpose != "demo" and identity is None):
        raise ValueError("Verified identity required")
    with engine_for(purpose).begin() as connection:
        if purpose != "import":
            # Version metadata and sales must come from the same replacement snapshot.
            connection.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        role = connection.execute(text(
            "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )).one()
        if role[0] != ROLES[purpose] or role[1] or role[2]:
            raise RuntimeError("Unsafe database role")
        connection.execute(text("SELECT set_config('statement_timeout', '5000', true)"))
        connection.execute(text("SELECT set_config('lock_timeout', '1000', true)"))
        claims = {"sub": str(identity.id), "role": "authenticated"} if identity else {}
        connection.execute(text("SELECT set_config('request.jwt.claims', :claims, true)"),
                           {"claims": json.dumps(claims)})
        yield connection
