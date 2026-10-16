import unittest

import httpx

from app.main import app

class HealthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_health_contract(self):
        response = await self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok", "service": "hisaabai-api"})


    async def test_local_frontend_origin_is_allowed(self):
        response = await self.client.get("/health", headers={"Origin": "http://127.0.0.1:3000"})
        self.assertEqual(response.headers["access-control-allow-origin"], "http://127.0.0.1:3000")


    async def test_foreign_origin_is_not_allowed(self):
        response = await self.client.get("/health", headers={"Origin": "https://untrusted.example"})
        self.assertNotIn("access-control-allow-origin", response.headers)
