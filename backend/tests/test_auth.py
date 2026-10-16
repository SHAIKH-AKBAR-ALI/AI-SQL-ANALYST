import os
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from app.main import app


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.env = patch.dict(os.environ, {"SUPABASE_URL": "https://auth.example", "SUPABASE_PUBLISHABLE_KEY": "public"})
        self.env.start()

    async def asyncTearDown(self):
        self.env.stop()
        await self.client.aclose()

    async def test_missing_token_denied_on_all_private_routes(self):
        for path in ("/me", "/datasets", f"/datasets/{uuid4()}"):
            response = await self.client.request("GET", path)
            self.assertEqual(response.status_code, 401)

    async def test_invalid_expired_or_forged_token_denied(self):
        for token in ("invalid", "expired", "forged"):
            with patch("app.auth.httpx.AsyncClient.get", new=AsyncMock(return_value=httpx.Response(401))):
                response = await self.client.request("GET", "/me", headers={"Authorization": f"Bearer {token}"})
                self.assertEqual(response.status_code, 401)

    async def test_identity_comes_only_from_auth_server(self):
        user_id = str(uuid4())
        with patch("app.auth.httpx.AsyncClient.get", new=AsyncMock(return_value=httpx.Response(200, json={
            "id": user_id, "email": "test@example.invalid", "role": "authenticated", "is_anonymous": False,
        }))):
            response = await self.client.request("GET", "/me?user_id=attacker", headers={"Authorization": "Bearer valid"})
            self.assertEqual(response.json()["id"], user_id)

    async def test_anonymous_auth_user_rejected(self):
        with patch("app.auth.httpx.AsyncClient.get", new=AsyncMock(return_value=httpx.Response(200, json={
            "id": str(uuid4()), "role": "authenticated", "is_anonymous": True,
        }))):
            self.assertEqual((await self.client.request("GET", "/me", headers={"Authorization": "Bearer anonymous"})).status_code, 401)

    async def test_auth_outage_fails_closed(self):
        with patch("app.auth.httpx.AsyncClient.get", new=AsyncMock(side_effect=httpx.ConnectError("private server error"))):
            response = await self.client.request("GET", "/me", headers={"Authorization": "Bearer valid"})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private server error", response.text)

    async def test_bearer_cors_preflight(self):
        response = await self.client.options("/datasets", headers={
            "Origin": "http://127.0.0.1:3000", "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        })
        self.assertEqual(response.status_code, 200)
