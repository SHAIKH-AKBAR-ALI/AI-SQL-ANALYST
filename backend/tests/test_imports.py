"""Actual local Auth/Postgres import checks, with isolated disposable accounts."""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from uuid import UUID, uuid4
from fastapi.testclient import TestClient

from sqlalchemy import text

from app.database import user_transaction
from app.imports import CommitRequest, commit_import
from app.sql_executor import QueryError, execute_query
from app.main import app
from app.history import PrivateQuestion, ask_private, read_history
from test_agent import FakeModel, proposal
from test_import_files import file_body, MAPPING, CSV
import test_isolation


@unittest.skipUnless(os.getenv('PHASE2_INTEGRATION') == '1', 'Requires local Supabase; see README')
class ImportIntegrationTests(unittest.TestCase):
    def setUp(self):
        app.middleware_stack = None

    @classmethod
    def setUpClass(cls):
        # Reuse fixture lifecycle, not inherited tests or shared test-class state.
        test_isolation.IsolationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        test_isolation.IsolationTests.tearDownClass.__func__(cls)

    def make_import(self, source=None, **kwargs):
        source = source or CSV.replace('Milk', 'Fixture-' + str(uuid4()))
        return CommitRequest(**file_body(source), mapping=MAPPING, amount_confirmed=True,
                             confirmed=True, name='Imported fixture', **kwargs)

    def save(self, body, user=None):
        result = commit_import(body, user or self.users[0])
        identifier = UUID(result['dataset_id'])
        if identifier not in self.dataset_ids:
            self.dataset_ids.append(identifier)
        return result

    def test_commit_repeat_and_concurrent_submissions_keep_exact_totals(self):
        body = self.make_import()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.save(body), range(2)))
        self.assertEqual(results[0]['dataset_id'], results[1]['dataset_id'])
        self.assertEqual(sum(result['duplicate'] for result in results), 1)
        result = execute_query('SELECT sum(amount), count(*) FROM sales', UUID(results[0]['dataset_id']), self.users[0])
        self.assertEqual(result['rows'], [['300.00', 2]])

    def test_replacement_versions_idempotency_and_owner_checks(self):
        first = self.save(self.make_import())
        identifier = UUID(first['dataset_id'])
        replacement = self.make_import(replace_dataset_id=identifier, expected_version=1, replace_confirmed=True)
        for user in (self.users[1],):
            with self.assertRaises(QueryError) as error:
                commit_import(replacement, user)
            self.assertEqual(error.exception.status, 404)
        second = self.save(replacement)
        self.assertEqual(second['dataset_version'], 2)
        self.assertTrue(self.save(replacement)['duplicate'])
        with self.assertRaises(QueryError) as error:
            self.save(self.make_import(replace_dataset_id=identifier, expected_version=1, replace_confirmed=True))
        self.assertEqual(error.exception.status, 409)
        self.assertEqual(execute_query('SELECT sum(amount) FROM sales', identifier, self.users[0])['rows'], [['300.00']])
        for target in (self.dataset_ids[1], UUID('00000000-0000-0000-0000-000000000001')):
            with self.assertRaises(QueryError):
                commit_import(self.make_import(replace_dataset_id=target, expected_version=1, replace_confirmed=True), self.users[0])

    def test_invalid_and_mid_transaction_failure_preserve_old_rows(self):
        first = self.save(self.make_import())
        identifier = UUID(first['dataset_id'])
        invalid = self.make_import(CSV.replace('120', '-1'), replace_dataset_id=identifier, expected_version=1, replace_confirmed=True)
        with self.assertRaises(QueryError):
            self.save(invalid)
        import app.imports as imports
        real_validate = imports.validate_file
        def invalid_after_validation(body):
            report, rows = real_validate(body)
            rows[-1]['quantity'] = -1  # DB constraint fails after old rows were deleted.
            return report, rows
        with patch('app.imports.validate_file', side_effect=invalid_after_validation), self.assertRaises(QueryError):
            self.save(self.make_import(replace_dataset_id=identifier, expected_version=1, replace_confirmed=True))
        result = execute_query('SELECT sum(amount), count(*) FROM sales', identifier, self.users[0])
        self.assertEqual(result['rows'], [['300.00', 2]])
        self.assertEqual(result['dataset_version'], 1)

    def test_repeated_identical_sales_are_preserved_and_changed_meaning_is_blocked(self):
        source = CSV.splitlines()[0] + '\n' + CSV.splitlines()[1] + '\n' + CSV.splitlines()[1] + '\n#' + str(uuid4())
        source = source.rsplit('\n#', 1)[0].replace('Milk', str(uuid4()))
        body = self.make_import(source)
        saved = self.save(body)
        self.assertEqual(execute_query('SELECT sum(amount), count(*) FROM sales', UUID(saved['dataset_id']), self.users[0])['rows'], [['240.00', 2]])
        with self.assertRaises(QueryError):
            self.save(body.model_copy(update={'date_format': 'DMY'}))

    def test_http_upload_to_answer_and_versioned_owner_history(self):
        body = self.make_import()
        headers = {'Authorization': f'Bearer {self.tokens[0]}'}
        with TestClient(app) as client:
            raw = body.model_dump(mode='json')
            file = {key: raw[key] for key in ('filename', 'content', 'sheet')}
            self.assertEqual(client.post('/imports/preview', json=file).status_code, 401)
            self.assertEqual(client.post('/imports/preview', headers=headers, json=file).json()['row_count'], 2)
            validation_body = {key: raw[key] for key in ('filename', 'content', 'sheet', 'mapping', 'date_format', 'default_unit', 'currency', 'amount_confirmed', 'adjustment_note')}
            self.assertTrue(client.post('/imports/validate', headers=headers, json=validation_body).json()['valid'])
            imported = client.post('/imports/commit', headers=headers, json=raw)
            self.assertEqual(imported.status_code, 200, imported.text)
            identifier = UUID(imported.json()['dataset_id'])
            self.dataset_ids.append(identifier)
            path = f'/datasets/{identifier}'
            self.assertEqual(client.get(path, headers=headers).json()['row_count'], 2)
            self.assertEqual(client.get(path + '/imports', headers=headers).json()['imports'][0]['accepted_rows'], 2)
            model = FakeModel(proposal(sql='SELECT sum(amount) AS revenue FROM sales'),
                              {'decision': 'approve', 'message': 'Correct'}, {'template': 'Revenue is {r0c0} INR.'})
            with patch('app.agent.model_for_request', return_value=(model, 'fake')):
                self.assertEqual(client.post(path + '/ask', headers=headers, json={'question': 'Total revenue?'}).status_code, 422)
                foreign = {'Authorization': f'Bearer {self.tokens[1]}'}
                self.assertEqual(client.post(path + '/ask', headers=foreign, json={'question': 'Total revenue?', 'ai_consent': True}).status_code, 404)
                self.assertEqual(len(model.calls), 0)
                result = client.post(path + '/ask', headers=headers, json={'question': 'Total revenue?', 'ai_consent': True})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['result']['rows'], [['300.00']])
            self.assertEqual(len(model.calls), 3)
            self.assertNotIn('Fictional demo products', model.calls[0][1][0][1])
            history = client.get(path + '/history', headers=headers).json()['history']
            self.assertEqual(history[0]['status'], 'succeeded')
            self.assertEqual(history[0]['dataset_version'], 1)
            self.assertEqual(client.get(path + '/history', headers=foreign).status_code, 404)
            self.assertEqual(client.get(path + '/imports', headers=foreign).status_code, 404)
            self.save(self.make_import(replace_dataset_id=identifier, expected_version=1, replace_confirmed=True))
            history = client.get(path + '/history', headers=headers).json()['history']
            self.assertEqual((history[0]['dataset_version'], history[0]['current_version']), (1, 2))

    def test_failed_questions_saved_without_raw_provider_error(self):
        imported = self.save(self.make_import())
        identifier = UUID(imported['dataset_id'])
        with patch('app.history.ask_demo', side_effect=QueryError(429, 'AI rate limit reached')), self.assertRaises(QueryError):
            ask_private(PrivateQuestion(question='Total revenue?', ai_consent=True), identifier, self.users[0])
        history = read_history(identifier, self.users[0])['history']
        self.assertEqual(history[0]['status'], 'failed')
        self.assertIsNone(history[0]['sql_text'])

    def test_private_clarification_resume_owner_and_version(self):
        imported = self.save(self.make_import())
        identifier = UUID(imported['dataset_id'])
        model = FakeModel(proposal(status='clarify', sql='', message='Which year?'))
        with patch('app.agent.model_for_request', return_value=(model, 'fake')):
            first = ask_private(PrivateQuestion(question='October sales?', ai_consent=True), identifier, self.users[0])
        resumed = PrivateQuestion(question='2026', ai_consent=True, conversation_id=first['conversation_id'])
        model = FakeModel(proposal(sql='SELECT sum(amount) FROM sales'), {'decision': 'approve', 'message': 'ok'}, {'template': '{r0c0} INR'})
        with patch('app.agent.model_for_request', return_value=(model, 'fake')):
            second = ask_private(resumed, identifier, self.users[0])
        self.assertEqual(second['conversation_id'], first['conversation_id'])
        self.assertIn('which year?', model.calls[0][1][1][1].lower())
        self.assertEqual(second['result']['rows'], [['300.00']])
        with patch('app.agent.model_for_request') as provider, self.assertRaises(QueryError):
            ask_private(resumed, identifier, self.users[1])
        provider.assert_not_called()
        other = UUID(self.save(self.make_import())['dataset_id'])
        with self.assertRaises(QueryError):
            ask_private(resumed, other, self.users[0])
        self.save(self.make_import(replace_dataset_id=identifier, expected_version=1, replace_confirmed=True))
        with patch('app.agent.model_for_request') as provider, self.assertRaises(QueryError) as error:
            ask_private(resumed, identifier, self.users[0])
        self.assertEqual(error.exception.status, 409)
        provider.assert_not_called()

    def test_reader_keeps_matching_version_and_rows_during_replacement(self):
        imported = self.save(self.make_import())
        identifier = UUID(imported['dataset_id'])
        with user_transaction(self.users[0]) as connection:
            self.assertEqual(connection.execute(text('SELECT version FROM app.datasets WHERE id=:id'), {'id': identifier}).scalar_one(), 1)
            self.save(self.make_import(CSV.replace('Milk', str(uuid4())).replace('120', '140'),
                                      adjustment_note='Tax', replace_dataset_id=identifier, expected_version=1, replace_confirmed=True))
            self.assertEqual(connection.execute(text('SELECT sum(amount) FROM app.sales WHERE dataset_id=:id'), {'id': identifier}).scalar_one(), 300)
        result = execute_query('SELECT sum(amount) FROM sales', identifier, self.users[0])
        self.assertEqual(result['rows'], [['320.00']])
        self.assertEqual(result['dataset_version'], 2)
