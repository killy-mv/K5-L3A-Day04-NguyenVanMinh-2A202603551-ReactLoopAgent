"""Offline tests for throttled and resumable evaluation runs."""
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'evals'))
import run_evals


class EvalProgressTests(unittest.TestCase):
    def test_throttle_every_request_including_retry(self):
        class FakeProvider:
            def __init__(self):
                self.attempts = 0
            def complete(self, messages, schemas):
                self.attempts += 1
                if self.attempts == 1:
                    raise RuntimeError('Provider HTTP 429')
                return {'finish': 'stop'}
        class FakePacer:
            def __init__(self):
                self.calls = 0
            def wait(self):
                self.calls += 1
        provider = FakeProvider()
        pacer = FakePacer()
        with patch.object(run_evals.time, 'sleep') as sleep:
            result = run_evals.PacedProvider(provider, pacer, 1, 65).complete([], [])
        self.assertEqual(result['finish'], 'stop')
        self.assertEqual(pacer.calls, 2)
        sleep.assert_called_once_with(65)

    def test_resume_skips_scored_and_retries_unscored(self):
        calls = []
        def fake(case, args):
            calls.append(case['id'])
            score = 100 if case['id'] == 'E1' else (None if len(calls) == 2 else 80)
            return {'id': case['id'], 'question': case['question'],
                    'expected': case['expected'], 'grade': {'score': score,
                    'status': 'scored' if score is not None else 'unscored'}}
        with tempfile.TemporaryDirectory() as tmp:
            output = str(Path(tmp) / 'eval.json')
            # Simulate interruption on E3 and preserve E1/E2 checkpoints.
            with patch.object(run_evals, 'run_case', side_effect=fake), patch.object(
                sys, 'argv', ['run_evals.py', '--model', 'test', '--output', output]):
                with patch.object(run_evals, 'read_json', wraps=run_evals.read_json), contextlib.redirect_stdout(io.StringIO()):
                    # Fully test the 22-case run without external requests.
                    result = run_evals.main()
            self.assertEqual(result, 1)
            data = json.loads(Path(output).read_text(encoding='utf-8'))
            self.assertEqual(data['summary']['total_cases'], 22)
            self.assertEqual(data['summary']['scored_cases'], 21)
            self.assertEqual(len(list(Path(tmp, 'eval_cases').glob('*.json'))), 22)
            count = len(calls)
            with patch.object(run_evals, 'run_case', side_effect=fake), patch.object(
                sys, 'argv', ['run_evals.py', '--model', 'test', '--output', output, '--resume']), contextlib.redirect_stdout(io.StringIO()):
                result = run_evals.main()
            self.assertEqual(result, 0)
            self.assertEqual(calls[count:], ['E2'])
            data = json.loads(Path(output).read_text(encoding='utf-8'))
            self.assertEqual(data['summary']['scored_cases'], 22)
            self.assertIsNotNone(data['summary']['score_0_100'])
            with patch.object(sys, 'argv', ['run_evals.py', '--model', 'another-model',
                         '--output', output, '--resume']), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    run_evals.main()


if __name__ == '__main__':
    unittest.main()
