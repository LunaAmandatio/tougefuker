"""Real subprocess protocol checks with synthetic responses; no network or lab execution."""
import json
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import ToolError
from codex_solver import CodexSolver, command_args, failure_error, normalize_model, validate_answer


CONTEXT = {'task_id': 'fixture', 'description': 'fixture description',
           'available_files': ['remote/file'],
           'files_context': {'remote/file': {'code': 'synthetic initial value', 'sha256': 'fixture'}}}
ANSWER = {'action': 'answer', 'files': [{'path': 'remote/file', 'code': 'synthetic replacement'}], 'read_files': []}


class TestCodexSolver(unittest.TestCase):
    def config(self, script):
        return {'command': [sys.executable, '-u', '-c', script], 'timeout': 2}

    def test_ephemeral_stdin_transport_parses_final_and_usage_without_output_files(self):
        script = ('import sys,json; p=sys.stdin.read(); '
                  'assert "fixture description" in p; '
                  'print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":'
                  + repr(json.dumps(ANSWER)) + '}})); '
                  'print(json.dumps({"type":"turn.completed","usage":{"input_tokens":20,"output_tokens":8}}))')
        events = []
        with patch('codex_solver.disabled_mcp_config', return_value=[]):
            solver = CodexSolver(self.config(script), lambda *e: events.append(e))
            answer, usage = solver.solve(CONTEXT, 'requested-model', threading.Event())
            args = command_args(solver.config, 'requested-model')
        self.assertEqual(answer, ANSWER)
        self.assertEqual(usage['output_tokens'], 8)
        self.assertTrue(events)
        self.assertIn('--ephemeral', args)
        self.assertIn('read-only', args)
        self.assertIn('features.shell_tool=false', args)
        self.assertIn('features.hooks=false', args)
        self.assertNotIn('--output-last-message', args)
        self.assertEqual(args[-1], '-')

    def test_invalid_and_unread_files_are_rejected(self):
        for value in (
                dict(ANSWER, files=[{'path': 'other/file', 'code': 'synthetic value'}]),
                {'action': 'read', 'files': [], 'read_files': ['outside']},
                {'action': 'answer', 'files': [], 'read_files': []}):
            with self.assertRaises(ToolError):
                validate_answer(value, CONTEXT)

    def test_cli_errors_do_not_expose_stderr_source_or_credentials(self):
        script = 'import sys; sys.stdin.read(); sys.stderr.write("MODEL_NOT_FOUND secret-source token=private"); sys.exit(1)'
        with patch('codex_solver.disabled_mcp_config', return_value=[]):
            with self.assertRaises(ToolError) as error:
                CodexSolver(self.config(script), lambda *e: None).solve(CONTEXT, 'requested-model', threading.Event())
        self.assertEqual(error.exception.code, 'model_not_found')
        self.assertEqual(error.exception.details['model'], 'requested-model')
        self.assertEqual(error.exception.details['exit_code'], 1)
        self.assertNotIn('private', str(error.exception))
        self.assertNotIn('secret-source', str(error.exception))

    def test_timeout_terminates_child(self):
        config = self.config('import sys,time; sys.stdin.read(); time.sleep(5)')
        config['timeout'] = 0.1
        with patch('codex_solver.disabled_mcp_config', return_value=[]):
            with self.assertRaises(ToolError) as error:
                CodexSolver(config, lambda *e: None).solve(CONTEXT, 'requested-model', threading.Event())
        self.assertEqual(error.exception.code, 'model_timeout')

    def test_model_tool_use_is_rejected(self):
        script = 'import sys,json; sys.stdin.read(); print(json.dumps({"type":"item.started","item":{"type":"command_execution"}}))'
        with patch('codex_solver.disabled_mcp_config', return_value=[]):
            with self.assertRaises(ToolError) as error:
                CodexSolver(self.config(script), lambda *e: None).solve(CONTEXT, 'requested-model', threading.Event())
        self.assertEqual(error.exception.code, 'unexpected_model_tool')

    def test_failure_categories_keep_safe_model_and_http_context(self):
        failures = [
            ('model_not_found', 'model_not_found'),
            ('The model abc is not supported', 'model_not_found'),
            ('no available channel for model abc', 'model_unavailable'),
            ('Not logged in', 'model_not_logged_in'),
            ('HTTP 401 unauthorized', 'model_auth_failed'),
            ('unexpected status 403', 'model_access_denied'),
            ('insufficient_quota', 'model_quota_exceeded'),
            ('HTTP 429', 'model_rate_limited'),
            ('HTTP status 503', 'model_service_unavailable'),
            ('error sending request: connection refused', 'model_connection_failed'),
            ('request timed out', 'model_timeout'),
            ('unexpected argument --fixture', 'codex_cli_incompatible'),
            ('unclassified provider failure', 'model_failed')]
        for raw, code in failures:
            with self.subTest(raw=raw):
                error = failure_error(raw + ' token=private secret-source', 'chosen-model', 1)
                self.assertEqual(error.code, code)
                self.assertIn('chosen-model', str(error))
                self.assertNotIn('private', str(error))
                self.assertNotIn('secret-source', str(error))
                self.assertEqual(error.details['model'], 'chosen-model')
        self.assertEqual(failure_error('HTTP status 503', 'chosen-model').details['http_status'], 503)

    def test_structured_turn_failure_raises_provider_exception_without_waiting_for_exit(self):
        script = ('import sys,json,time; sys.stdin.read(); '
                  'print(json.dumps({"type":"turn.failed","error":{"code":"model_not_found",'
                  '"message":"fixture unavailable token=private"}}),flush=True); time.sleep(5)')
        with patch('codex_solver.disabled_mcp_config', return_value=[]):
            with self.assertRaises(ToolError) as error:
                CodexSolver(self.config(script), lambda *e: None).solve(CONTEXT, 'chosen-model', threading.Event())
        self.assertEqual(error.exception.code, 'model_not_found')
        self.assertEqual(error.exception.details['provider_code'], 'model_not_found')
        self.assertNotIn('private', str(error.exception))

    def test_invalid_or_empty_model_never_falls_back(self):
        for model in ('', '   ', 'two models', 'model\nextra', 'x' * 121, None):
            with self.subTest(model=model), self.assertRaises(ToolError):
                normalize_model(model)
        self.assertEqual(normalize_model('  custom/model-1.0  '), 'custom/model-1.0')


if __name__ == '__main__':
    unittest.main()
