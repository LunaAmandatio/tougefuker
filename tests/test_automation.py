"""End-to-end controller decisions using an in-memory webpage/solver double."""
import concurrent.futures
import copy
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from automation import AutoController
from core import BASE, ToolError, digest


def context(tid='fixture', path='remote/file', code='synthetic initial value'):
    return {'task_id': tid, 'subject': 'fixture title', 'description': 'fixture description',
            'available_files': ['remote/file', 'remote/helper'], 'code': code,
            'current_file': {'path': path, 'sha256': digest(code), 'read_only': False}}


def answer(code='synthetic replacement', path='remote/file'):
    return {'action': 'answer', 'files': [{'path': path, 'code': code}], 'read_files': []}


class Worker:
    def __init__(self, inputs, selection=None, initial_context=None):
        self.engine = SimpleNamespace(base=BASE, cancel=threading.Event(), config={'codex': {'max_attempts': 3}})
        self.inputs = iter(inputs)
        self.calls = []
        self.selection = selection or {'restricted': True, 'ids': ['4115214']}
        self.initial_context = initial_context or context()

    def submit(self, command, args):
        self.calls.append((command, copy.deepcopy(args)))
        future = concurrent.futures.Future()
        if command == 'select':
            value = {'selection': self.selection}
        elif command == 'auto-context':
            value = self.initial_context
        elif command == 'context':
            value = context(path=args['path'], code='synthetic helper value')
        else:
            value = next(self.inputs)
            if 'code' in args:
                value = dict(value, sha256=digest(args['code']))
        future.set_result({'ok': True, **value})
        return future


class TestAutomation(unittest.TestCase):
    def run_loop(self, worker, answers, cancel_after_model=False):
        payloads = []
        controller = None
        class Solver:
            def __init__(self, *args):
                self.answers = iter(answers)
            def solve(self, payload, model, cancel):
                payloads.append(copy.deepcopy(payload))
                if cancel_after_model:
                    cancel.set()
                return next(self.answers), {'input_tokens': 10, 'output_tokens': 5}
        controller = AutoController(worker, Solver)
        with patch('diagnostics.record_error', return_value={'error_id': 'fixture'}):
            controller.start('requested-model')
            controller.thread.join(timeout=3)
        self.assertFalse(controller.state['running'])
        return controller, payloads

    def test_failure_feedback_reaches_next_model_call_and_success_stops_selection(self):
        worker = Worker([
            {'action': 'revise', 'evaluation': {'result': {'passed': False, 'message': 'fixture failure'}}},
            {'action': 'selection_complete', 'evaluation': {'result': {'passed': True}}}])
        controller, payloads = self.run_loop(worker, [answer(), answer('synthetic corrected value')])
        self.assertEqual(controller.state['phase'], 'complete')
        self.assertEqual(payloads[1]['feedback']['message'], 'fixture failure')
        self.assertEqual(payloads[1]['files_context']['remote/file']['code'], 'synthetic replacement')
        submissions = [args for command, args in worker.calls if command == 'input']
        self.assertEqual(submissions[1]['expected'], digest('synthetic replacement'))
        self.assertEqual(controller.state['usage']['input_tokens'], 20)

    def test_cancellation_after_model_never_submits(self):
        worker = Worker([])
        controller, _ = self.run_loop(worker, [answer()], cancel_after_model=True)
        self.assertEqual(controller.state['phase'], 'stopped')
        self.assertFalse(any(command == 'input' for command, args in worker.calls))

    def test_unchanged_answer_not_submitted(self):
        worker = Worker([{'action': 'revise', 'evaluation': {'result': {'passed': False}}}])
        controller, _ = self.run_loop(worker, [answer(), answer()])
        self.assertEqual(controller.state['error'], 'unchanged_answer')
        self.assertEqual(sum(command == 'input' for command, args in worker.calls), 1)

    def test_initial_unchanged_code_can_be_evaluated_once(self):
        worker = Worker([{'action': 'selection_complete', 'evaluation': {'result': {'passed': True}}}])
        controller, _ = self.run_loop(worker, [answer('synthetic initial value')])
        self.assertEqual(controller.state['phase'], 'complete')

    def test_multifile_edits_only_evaluate_after_last_save(self):
        worker = Worker([{'action': 'saved'}, {'action': 'selection_complete', 'evaluation': {'result': {'passed': True}}}])
        answers = [{'action': 'read', 'files': [], 'read_files': ['remote/helper']},
                   {'action': 'answer', 'files': [answer()['files'][0], answer('synthetic helper edit', 'remote/helper')['files'][0]], 'read_files': []}]
        controller, _ = self.run_loop(worker, answers)
        self.assertEqual(controller.state['phase'], 'complete')
        submissions = [args for command, args in worker.calls if command == 'input']
        self.assertFalse(submissions[0]['evaluate'])
        self.assertTrue(submissions[1]['evaluate'])

    def test_no_selection_stops_before_model_or_browser_context(self):
        worker = Worker([], selection={'restricted': False, 'ids': []})
        controller, payloads = self.run_loop(worker, [])
        self.assertEqual(controller.state['error'], 'empty_selection')
        self.assertEqual(payloads, [])
        self.assertEqual([command for command, args in worker.calls], ['select'])

    def test_last_code_level_advances_to_next_experiment_choice_and_revises(self):
        choice = {'task_id': 'choice-fixture', 'subject': '选择关卡', 'task_type': 'choice',
                  'experiment': {'homework_id': '4115215'},
                  'choices': [{'id': 'q', 'options': [{'position': 0}, {'position': 1}]}]}
        worker = Worker([
            {'action': 'next_experiment', 'evaluation': {'result': {'passed': True}}, 'context': choice},
            {'action': 'revise', 'evaluation': {'result': {'passed': False, 'message': 'fixture incorrect'}}},
            {'action': 'selection_complete', 'evaluation': {'result': {'passed': True}}}])
        choose = lambda p: {'action': 'choose', 'files': [], 'read_files': [],
                            'answers': [{'question_id': 'q', 'positions': [p]}]}
        controller, payloads = self.run_loop(worker, [answer(), choose(0), choose(1)])
        self.assertEqual(controller.state['phase'], 'complete')
        self.assertEqual(controller.state['completed_tasks'], 2)
        self.assertEqual(controller.state['homework_id'], '4115215')
        self.assertEqual(payloads[1]['task_type'], 'choice')
        self.assertEqual(payloads[1]['files_context'], {})
        self.assertEqual(payloads[2]['feedback']['message'], 'fixture incorrect')
        inputs = [args for command, args in worker.calls if command == 'input']
        self.assertNotIn('code', inputs[1])
        self.assertEqual(inputs[2]['answers'][0]['positions'], [1])

    def test_status_publishes_browser_progress_during_navigation(self):
        worker = Worker([])
        worker.engine.progress_snapshot = lambda: {'4115214': {'finished': 7}}
        self.assertEqual(AutoController(worker).status()['experiments']['4115214']['finished'], 7)

    def test_model_load_failure_stops_before_any_submission_and_reports_exception(self):
        worker = Worker([])
        class FailedSolver:
            def __init__(self, *args):
                pass
            def solve(self, payload, model, cancel):
                raise ToolError('模型 chosen-model 不存在', 'model_not_found', model=model,
                                provider_code='model_not_found', http_status=404)
        controller = AutoController(worker, FailedSolver)
        with patch('diagnostics.record_error', return_value={'error_id': 'fixture'}):
            controller.start('chosen-model')
            controller.thread.join(timeout=3)
        status = controller.status()
        self.assertFalse(status['automation']['running'])
        self.assertEqual(status['automation']['error'], 'model_not_found')
        self.assertEqual(status['automation']['error_details']['model'], 'chosen-model')
        self.assertEqual(status['automation']['error_details']['http_status'], 404)
        self.assertTrue(any(e['kind'] == 'error' and 'model_not_found' in e['text'] for e in status['events']))
        self.assertFalse(any(command == 'input' for command, args in worker.calls))

    def test_empty_model_is_rejected_before_starting_thread(self):
        controller = AutoController(Worker([]))
        with self.assertRaises(ToolError):
            controller.start('')
        self.assertIsNone(controller.thread)


if __name__ == '__main__':
    unittest.main()
