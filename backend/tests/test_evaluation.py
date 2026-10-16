import json
import sys
import unittest
import tempfile
from contextlib import nullcontext, redirect_stdout
from io import StringIO
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from evals.compare import equal_rows


class ComparatorTests(unittest.TestCase):
    def test_live_benchmark_stops_on_first_quota_error_and_keeps_incomplete_report(self):
        from scripts import evaluate_agent
        from app.sql_executor import QueryError
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'evals').mkdir()
            (root/'evals/cases.json').write_bytes((ROOT/'evals/cases.json').read_bytes())
            with patch.object(evaluate_agent, 'ROOT', root), \
                 patch.object(evaluate_agent, 'fixtures', return_value=nullcontext({'demo': (None, None)})), \
                 patch.object(evaluate_agent, 'ask_demo', side_effect=QueryError(429, 'AI rate limit reached.')) as ask, \
                 patch.object(evaluate_agent, 'groq_settings', return_value=('', 'test-model')), \
                 patch.object(sys, 'argv', ['evaluate_agent.py', '--live']), redirect_stdout(StringIO()):
                self.assertEqual(evaluate_agent.main(), 1)
            ask.assert_called_once()
            summary = json.loads(next((root/'evals/results').glob('*.summary.json')).read_text())
            self.assertEqual((summary['total'], summary['planned']), (1, 40))
            self.assertFalse(summary['complete'])
            self.assertEqual(summary['stopped_reason'], 'provider_rate_limit')

    def test_multisets_order_null_tolerance_and_precision(self):
        self.assertTrue(equal_rows([[1],[2],[1]],[[2],[1],[1]]))
        self.assertFalse(equal_rows([[1],[2],[1]],[[2],[1],[2]]))
        self.assertFalse(equal_rows([[1],[2]],[[2],[1]],ordered=True))
        self.assertFalse(equal_rows([[None]],[[0]]))
        self.assertTrue(equal_rows([['1.0000001']],[[1]]))
        self.assertFalse(equal_rows([['9007199254740993.01']],[['9007199254740993.02']]))
        self.assertFalse(equal_rows([[True]],[[1]]))
        self.assertFalse(equal_rows([['NaN']],[['NaN']]))

    def test_corpus_has_40_unique_multilingual_cases_and_fixed_expectations(self):
        cases = json.loads((ROOT/'evals/cases.json').read_text(encoding='utf-8'))
        self.assertEqual(len(cases),40)
        self.assertEqual(len({case['id'] for case in cases}),40)
        self.assertEqual({case['language'] for case in cases},{'en','hi','hinglish'})
        for case in cases:
            self.assertTrue(case['rationale'])
            if case['status']=='answered':
                self.assertEqual(set(case['expected']),{'demo','edge','empty'})
