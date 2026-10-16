import unittest

import httpx
from app.main import app


class UploadAPITests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        app.middleware_stack = None

    async def test_auth_and_body_boundaries(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            for path in ('/imports/preview', '/imports/validate', '/imports/commit', '/datasets/00000000-0000-0000-0000-000000000001/ask'):
                self.assertEqual((await client.post(path, json={})).status_code, 401)
            response = await client.post('/imports/preview', content=b'x' * (8 * 1024 * 1024 + 1))
            self.assertEqual(response.status_code, 413)
            self.assertNotIn('xxxxx', response.text)

    async def test_chunked_actual_size_limit(self):
        async def chunks():
            for _ in range(9):
                yield b'x' * (1024 * 1024)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            self.assertEqual((await client.post('/imports/preview', content=chunks())).status_code, 413)
