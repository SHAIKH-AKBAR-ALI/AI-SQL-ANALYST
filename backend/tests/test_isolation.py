"""Opt-in integration suite: PHASE2_INTEGRATION=1, local Supabase only."""
import json
import os
import secrets
import subprocess
import unittest
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import psycopg
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.auth import Identity
from app.database import engine_for, user_transaction
from app.main import app

ROOT = Path(__file__).resolve().parents[2]
DEMO = UUID("00000000-0000-0000-0000-000000000001")


@unittest.skipUnless(os.getenv("PHASE2_INTEGRATION") == "1", "Requires local Supabase; see README")
class IsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_dotenv(ROOT / "backend/.env", override=True)
        result = subprocess.run([str(ROOT / ('node_modules/.bin/supabase.cmd' if os.name == 'nt' else 'node_modules/.bin/supabase')), "status", "-o", "json"],
                                cwd=ROOT, capture_output=True, text=True, check=True)
        cls.status = json.loads(result.stdout)
        assert cls.status["API_URL"] == "http://127.0.0.1:55321", "Local test stack only"
        cls.admin_headers = {"apikey": cls.status["SERVICE_ROLE_KEY"],
                             "Authorization": f"Bearer {cls.status['SERVICE_ROLE_KEY']}"}
        cls.users = []
        cls.dataset_ids = []
        cls.conversations = []
        cls.tokens = []
        for index in range(2):
            email = f"phase2-{uuid4()}@example.invalid"
            password = secrets.token_urlsafe(24)
            response = httpx.post(f"{cls.status['API_URL']}/auth/v1/admin/users", headers=cls.admin_headers,
                                  json={"email": email, "password": password, "email_confirm": True}, timeout=10)
            response.raise_for_status()
            user = Identity(UUID(response.json()["id"]), email)
            cls.users.append(user)
            token = httpx.post(f"{cls.status['API_URL']}/auth/v1/token?grant_type=password",
                               headers={"apikey": cls.status["PUBLISHABLE_KEY"]},
                               json={"email": email, "password": password}, timeout=10)
            token.raise_for_status()
            cls.tokens.append(token.json()["access_token"])
            dataset_id, conversation = uuid4(), uuid4()
            cls.dataset_ids.append(dataset_id)
            cls.conversations.append(conversation)
            with user_transaction(user, "import") as connection:
                connection.execute(text("INSERT INTO app.datasets (id, owner_id, name) VALUES (:id, :owner, :name)"),
                                   {"id": dataset_id, "owner": user.id, "name": f"Isolation fixture {index}"})
                connection.execute(text("""INSERT INTO app.sales
                    (dataset_id, source_row, sale_date, product_name, quantity, unit, amount)
                    VALUES (:id, 1, '2026-10-01', 'Private product', 1, 'piece', 123)"""), {"id": dataset_id})
                connection.execute(text("""INSERT INTO app.imports
                    (dataset_id, owner_id, dataset_version, status, file_sha256)
                    VALUES (:id, :owner, 1, 'committed', :hash)"""),
                    {"id": dataset_id, "owner": user.id, "hash": str(index) * 64})
                connection.execute(text("""INSERT INTO app.conversations (id, dataset_id, owner_id, dataset_version)
                    VALUES (:cid, :id, :owner, 1)"""), {"cid": conversation, "id": dataset_id, "owner": user.id})
                connection.execute(text("""INSERT INTO app.query_runs
                    (conversation_id, dataset_id, owner_id, dataset_version, question, status)
                    VALUES (:cid, :id, :owner, 1, 'Total sales?', 'pending')"""),
                    {"cid": conversation, "id": dataset_id, "owner": user.id})

    @classmethod
    def tearDownClass(cls):
        for engine in (engine_for(p) for p in ("query", "import", "demo")):
            engine.dispose()
        with psycopg.connect(cls.status["DB_URL"]) as connection:
            for table in ("query_runs", "conversations", "imports", "sales", "datasets"):
                connection.execute(f"DELETE FROM app.{table} WHERE " +
                                   ("id" if table == "datasets" else "dataset_id") + " = ANY(%s)", (cls.dataset_ids,))
        for user in cls.users:
            response = httpx.delete(f"{cls.status['API_URL']}/auth/v1/admin/users/{user.id}", headers=cls.admin_headers)
            response.raise_for_status()

    def test_two_user_isolation_on_every_table(self):
        for index, user in enumerate(self.users):
            with user_transaction(user) as connection:
                ids = set(connection.execute(text("SELECT id FROM app.datasets")).scalars())
                self.assertEqual(ids, {self.dataset_ids[index], DEMO})
                self.assertEqual(connection.execute(text("SELECT count(*) FROM app.sales")).scalar_one(), 7)
                for table in ("imports", "conversations", "query_runs"):
                    owners = set(connection.execute(text(f"SELECT owner_id FROM app.{table}")).scalars())
                    self.assertEqual(owners, {user.id})

    def test_no_identity_denied_and_pool_context_does_not_leak(self):
        with self.assertRaises(ValueError):
            with user_transaction(None):
                pass
        with user_transaction(self.users[0]) as connection:
            self.assertEqual(connection.execute(text("SELECT count(*) FROM app.datasets")).scalar_one(), 2)
        with engine_for("query").begin() as connection:
            self.assertEqual(connection.execute(text("SELECT count(*) FROM app.datasets")).scalar_one(), 0)
            self.assertEqual(connection.execute(text("SELECT count(*) FROM app.sales")).scalar_one(), 0)

    def test_demo_boundaries_and_known_totals(self):
        with user_transaction(None, "demo") as connection:
            self.assertEqual(set(connection.execute(text("SELECT id FROM app.datasets")).scalars()), {DEMO})
            row = connection.execute(text("SELECT count(*), sum(amount) FROM app.sales")).one()
            self.assertEqual(tuple(row), (6, Decimal("2100.00")))
            october = connection.execute(text("""SELECT sum(amount) FROM app.sales
                WHERE sale_date >= '2026-10-01' AND sale_date < '2026-11-01'""")).scalar_one()
            self.assertEqual(october, Decimal("1440.00"))
            units = dict(connection.execute(text("SELECT unit, sum(quantity) FROM app.sales GROUP BY unit")).all())
            self.assertEqual(units, {"litre": Decimal("27.500"), "kg": Decimal("10.000"), "piece": Decimal("3.000")})

    def test_query_and_demo_roles_cannot_write_or_escalate(self):
        for purpose, user in (("query", self.users[0]), ("demo", None)):
            for sql in ("DELETE FROM app.sales", "SET ROLE hisaab_import", "SELECT * FROM auth.users",
                        "TRUNCATE app.sales", "CREATE TABLE app.attack (id int)"):
                with self.assertRaises(DBAPIError):
                    with user_transaction(user, purpose) as connection:
                        connection.execute(text(sql))
            with user_transaction(user, purpose) as connection:
                self.assertFalse(connection.execute(text("SELECT has_table_privilege(current_user, 'app.sales', 'INSERT')")).scalar_one())

    def test_invalid_sales_values_rejected(self):
        for quantity, unit, amount in ((0, "piece", 1), (-1, "piece", 1), (1, "unknown", 1),
                                       (1, "piece", -1), ("NaN", "piece", 1), (1, "piece", "NaN")):
            with self.assertRaises(DBAPIError):
                with user_transaction(self.users[0], "import") as connection:
                    connection.execute(text("""INSERT INTO app.sales
                        (dataset_id, source_row, sale_date, product_name, quantity, unit, amount)
                        VALUES (:id, 999, '2026-10-01', 'Invalid', :quantity, :unit, :amount)"""),
                        {"id": self.dataset_ids[0], "quantity": quantity, "unit": unit, "amount": amount})

    def test_import_cannot_mutate_demo_or_other_user(self):
        with user_transaction(self.users[0], "import") as connection:
            for dataset in (DEMO, self.dataset_ids[1]):
                response = connection.execute(text("UPDATE app.datasets SET name='hacked' WHERE id=:id"), {"id": dataset})
                self.assertEqual(response.rowcount, 0)
                response = connection.execute(text("DELETE FROM app.sales WHERE dataset_id=:id"), {"id": dataset})
                self.assertEqual(response.rowcount, 0)
        for dataset in (DEMO, self.dataset_ids[1]):
            with self.assertRaises(DBAPIError):
                with user_transaction(self.users[0], "import") as connection:
                    connection.execute(text("""INSERT INTO app.sales
                        (dataset_id, source_row, sale_date, product_name, quantity, unit, amount)
                        VALUES (:id, 999, '2026-10-01', 'Attack', 1, 'piece', 1)"""), {"id": dataset})

    def test_owner_cannot_be_reassigned_or_demo_created(self):
        for query, params in (
            ("UPDATE app.datasets SET owner_id=:owner WHERE id=:id", {"owner": self.users[1].id, "id": self.dataset_ids[0]}),
            ("INSERT INTO app.datasets (name,is_demo) VALUES ('Fake demo',true)", {}),
            ("""INSERT INTO app.conversations (dataset_id,owner_id,dataset_version)
                VALUES (:id,:owner,1)""", {"id": self.dataset_ids[1], "owner": self.users[0].id}),
        ):
            with self.assertRaises(DBAPIError):
                with user_transaction(self.users[0], "import") as connection:
                    connection.execute(text(query), params)

    def test_unsafe_database_role_refused(self):
        from unittest.mock import patch
        for purpose in ("query", "import", "demo"):
            engine_for(purpose).dispose()
        engine_for.cache_clear()
        try:
            with patch.dict(os.environ, {"QUERY_DATABASE_URL": self.status["DB_URL"].replace("postgresql://", "postgresql+psycopg://")}):
                with self.assertRaisesRegex(RuntimeError, "Unsafe database role"):
                    with user_transaction(self.users[0]):
                        pass
        finally:
            engine_for("query").dispose()
            engine_for.cache_clear()

    def test_data_api_cannot_access_app_schema(self):
        for key, token in ((self.status["PUBLISHABLE_KEY"], self.tokens[0]),
                           (self.status["PUBLISHABLE_KEY"], self.status["ANON_KEY"])):
            response = httpx.get(f"{self.status['API_URL']}/rest/v1/datasets", headers={
                "apikey": key, "Authorization": f"Bearer {token}", "Accept-Profile": "app",
            })
            self.assertIn(response.status_code, (400, 403, 404, 406))

    def test_real_auth_and_dataset_api(self):
        import asyncio
        import base64
        import hashlib
        import hmac
        import time

        def encode(value):
            return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=")

        expired_body = b".".join((encode({"alg": "HS256", "typ": "JWT"}), encode({
            "sub": str(self.users[0].id), "aud": "authenticated", "role": "authenticated",
            "iss": f"{self.status['API_URL']}/auth/v1", "exp": int(time.time()) - 3600,
        })))
        signature = base64.urlsafe_b64encode(hmac.new(
            self.status["JWT_SECRET"].encode(), expired_body, hashlib.sha256).digest()).rstrip(b"=")
        expired_token = (expired_body + b"." + signature).decode()

        async def check():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                for index, token in enumerate(self.tokens):
                    headers = {"Authorization": f"Bearer {token}"}
                    me = await client.get("/me", headers=headers)
                    self.assertEqual(me.status_code, 200)
                    self.assertEqual(me.json()["id"], str(self.users[index].id))
                    response = await client.get("/datasets", headers=headers)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual({d["id"] for d in response.json()["datasets"]}, {str(DEMO), str(self.dataset_ids[index])})
                    response = await client.get(f"/datasets/{self.dataset_ids[1-index]}", headers=headers)
                    self.assertEqual(response.status_code, 404)
                response = await client.get("/demo/datasets")
                self.assertEqual({d["id"] for d in response.json()["datasets"]}, {str(DEMO)})
                for token in ("forged", expired_token):
                    response = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
                    self.assertEqual(response.status_code, 401)
        asyncio.run(check())
