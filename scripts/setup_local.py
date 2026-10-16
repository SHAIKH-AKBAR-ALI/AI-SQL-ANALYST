"""Provision only this project's local Supabase roles; never print credentials."""
import json
import os
import secrets
import subprocess
from pathlib import Path

import psycopg
from psycopg import sql
from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]


def local_status():
    result = subprocess.run([str(ROOT / ('node_modules/.bin/supabase.cmd' if os.name == 'nt' else 'node_modules/.bin/supabase')), "status", "-o", "json"],
                            cwd=ROOT, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def configure():
    status = local_status()
    if status["API_URL"] != "http://127.0.0.1:55321":
        raise RuntimeError("Expected project-local Supabase")
    local_file = ROOT / "database/.env.local"
    existing = dotenv_values(local_file)
    values = {"SUPABASE_URL": status["API_URL"],
              "SUPABASE_PUBLISHABLE_KEY": status.get("PUBLISHABLE_KEY") or status["ANON_KEY"]}
    passwords = {}
    for purpose in ("query", "import", "demo"):
        password = existing.get(f"{purpose.upper()}_PASSWORD") or secrets.token_hex(24)
        passwords[purpose] = password
        values[f"{purpose.upper()}_DATABASE_URL"] = f"postgresql+psycopg://hisaab_{purpose}:{password}@127.0.0.1:55322/postgres"
    backend_file = ROOT / "backend/.env"
    frontend_file = ROOT / "frontend/.env.local"
    targets = [(backend_file, values), (frontend_file, {
        "NEXT_PUBLIC_SUPABASE_URL": values["SUPABASE_URL"],
        "NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY": values["SUPABASE_PUBLISHABLE_KEY"],
    })]
    for path, settings in targets:
        current = dotenv_values(path)
        for name, value in settings.items():
            if current.get(name) and current[name] != value:
                raise RuntimeError(f"Preserving customized {name} in {path.name}; configure manually")
    with psycopg.connect(status["DB_URL"]) as connection:
        for purpose, password in passwords.items():
            connection.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(f"hisaab_{purpose}"), sql.Literal(password)))
    for purpose, password in passwords.items():
        set_key(str(local_file), f"{purpose.upper()}_PASSWORD", password)
    for path, settings in targets:
        for name, value in settings.items():
            set_key(str(path), name, value)
    print("Local restricted roles and app env configured. Credentials remain in ignored files.")


if __name__ == "__main__":
    configure()
