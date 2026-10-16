import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from app.file_jobs import preview_file, run_file_job
from app.import_files import FileRequest
from app.limits import UsageLimits
from app.sql_executor import QueryError
from app.telemetry import CURRENT, export_record, new_record, stage
from test_import_files import file_body, CSV


class LimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_rate_limit_cannot_be_reset_by_forged_forwarded_header(self):
        async def ok(scope, receive, send):
            from fastapi.responses import JSONResponse
            await JSONResponse({'ok': True})(scope, receive, send)
        app = UsageLimits(ok)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            for i in range(12):
                self.assertEqual((await client.post('/demo/datasets/x/ask', headers={'X-Forwarded-For': str(i)})).status_code, 200)
            response = await client.post('/demo/datasets/x/ask')
            self.assertEqual(response.status_code, 429)
            self.assertEqual(response.headers['retry-after'], '60')
            self.assertEqual((await client.get('/health')).status_code, 200)

    async def test_concurrency_slot_released_after_failure(self):
        active = asyncio.Event()
        release = asyncio.Event()
        async def blocked(scope, receive, send):
            active.set()
            await release.wait()
            from fastapi.responses import JSONResponse
            await JSONResponse({'ok': True})(scope, receive, send)
        app = UsageLimits(blocked)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            first = asyncio.create_task(client.post('/imports/preview'))
            await active.wait()
            self.assertEqual((await client.post('/imports/preview')).status_code, 503)
            release.set()
            self.assertEqual((await first).status_code, 200)
            self.assertEqual((await client.post('/imports/preview')).status_code, 200)
            self.assertEqual(sum(app.active.values()), 0)


class WorkerTelemetryTests(unittest.TestCase):
    def test_actual_langfuse_sdk_exports_allowlisted_spans(self):
        from langfuse import Langfuse
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        client = Langfuse(public_key='pk-lf-local-test',secret_key='sk-lf-local-test',
            base_url='http://127.0.0.1:1',tracer_provider=provider,span_exporter=exporter)
        record = new_record()
        record['stages'] = [dict(stage='model_proposal',outcome='ok',input_tokens=100,output_tokens=20,latency_ms=15)]
        with patch('app.telemetry.trace_client',return_value=client):
            export_record(record,'test-model','phase9-test','answered')
        client.flush()
        spans = exporter.get_finished_spans()
        self.assertEqual({span.name for span in spans},{'question','model_proposal'})
        self.assertTrue(all(format(span.context.trace_id,'032x')==record['trace_id'] for span in spans))
        self.assertNotIn('langfuse.observation.input',str([span.attributes for span in spans]))
        provider.shutdown()

    def test_actual_spawned_parser_and_invalid_file(self):
        response = preview_file(FileRequest(**file_body(CSV)))
        self.assertIn('headers', response)
        with self.assertRaises(QueryError):
            preview_file(FileRequest(filename='bad.xlsx', content='aGVsbG8='))

    def test_worker_timeout_terminates_process(self):
        with patch('app.file_jobs.multiprocessing.get_context') as context:
            parent, child = context.return_value.Pipe.return_value = (unittest.mock.Mock(), unittest.mock.Mock())
            parent.poll.return_value = False
            process = context.return_value.Process.return_value
            process.pid = 42
            process.is_alive.return_value = True
            with self.assertRaises(QueryError) as error:
                run_file_job('preview_file', {})
            self.assertEqual(error.exception.status, 408)
            process.terminate.assert_called_once()
            process.join.assert_called_once()

    def test_trace_export_has_no_private_payload_even_on_error(self):
        record = new_record()
        token = CURRENT.set(record)
        try:
            with self.assertRaises(ValueError), stage('sql_validation'):
                raise ValueError('SECRET customer@example.invalid SELECT private_data')
        finally:
            CURRENT.reset(token)
        with patch('app.telemetry.trace_client') as client:
            export_record(record, 'model', 'version', 'error')
            exported = str(client.mock_calls)
        self.assertNotIn('SECRET', exported)
        self.assertNotIn('customer@', exported)
        self.assertNotIn('private_data', exported)
        self.assertIn('sql_validation', exported)
        self.assertEqual(record['stages'][0]['outcome'], 'error')
        self.assertIsNone(record['cost_usd'])
