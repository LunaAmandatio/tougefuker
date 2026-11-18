import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from educoder_agent.core import BASE, Engine, SnapshotStore, ToolError, compact_result, digest, task_id, validate_url
from educoder_agent.cli import parse_input


class TestSnapshots(unittest.TestCase):
    def setUp(self):
        root = BASE / '.state' / 'tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.store = SnapshotStore(self.temp.name)
        self.snapshot = {'url': 'https://www.educoder.net/tasks/J2F8SUKL/4115214/abc123',
                         'description': '输入两个整数，输出它们的和。\n保留输入输出格式。',
                         'current_path': 'src/main.py', 'content': 'print(1)\n', 'language': 'python',
                         'files': ['src/main.py'], 'game_status': 0}

    def test_delta_is_explicit_and_keeps_new_code(self):
        first = self.store.context(self.snapshot)
        same = self.store.context(self.snapshot, first['revision'])
        self.assertEqual(same['changed'], {})
        self.snapshot['content'] = 'print(2)\n'
        changed = self.store.context(self.snapshot, first['revision'])
        self.assertEqual(changed['changed']['code'], 'print(2)\n')
        self.assertNotIn('description', changed['changed'])

    def test_unknown_revision_returns_full_context(self):
        value = self.store.context(self.snapshot, 'expired')
        self.assertFalse(value['delta'])
        self.assertEqual(value['description'], self.snapshot['description'])

    def test_context_persists_only_hashes_never_source_or_question(self):
        result = self.store.context(self.snapshot)
        self.assertNotIn('local_file', result['current_file'])
        for p in Path(self.temp.name).rglob('*'):
            if p.is_file():
                stored = p.read_text(encoding='utf-8')
                self.assertNotIn(self.snapshot['content'].strip(), stored)
                self.assertNotIn(self.snapshot['description'].splitlines()[0], stored)
                self.assertNotIn('local_file', stored)

    def test_remote_path_never_creates_a_source_file(self):
        self.snapshot['current_path'] = '../../outside.py'
        result = self.store.context(self.snapshot)
        self.assertEqual(result['current_file']['path'], '../../outside.py')
        self.assertEqual(list(Path(self.temp.name).rglob('*.py')), [])

    def test_result_excerpt_does_not_drop_pass_status_or_claim_failure_passed(self):
        result = compact_result({'status': 0, 'message': 'compile error' * 1000}, 300)
        self.assertFalse(result['passed'])
        self.assertTrue(result['truncated'])
        self.assertEqual(result['status'], 0)
        self.assertTrue(compact_result({'status': 2})['passed'])
        self.assertFalse(compact_result({'status': 1})['passed'])

    def test_unknown_case_schema_does_not_become_passed(self):
        result = compact_result({'status': -1, 'test_sets': [{'result': None}]})
        self.assertFalse(result['passed'])
        self.assertEqual(result['passed_cases'], 0)


class TestGuards(unittest.TestCase):
    def setUp(self):
        selection = patch.object(Engine, 'selection', return_value={'restricted': False, 'ids': []})
        selection.start()
        self.addCleanup(selection.stop)

    def test_url_guards(self):
        for url in ('http://www.educoder.net/tasks/abc', 'https://www.educoder.net.evil.test/tasks/abc',
                    'https://www.educoder.net/account', 'https://x@www.educoder.net/tasks/abc'):
            with self.assertRaises(ToolError):
                validate_url(url)
        self.assertEqual(task_id('https://www.educoder.net/tasks/C/H/abc?x=1'), 'abc')
        with self.assertRaises(ToolError):
            task_id('https://www.educoder.net/tasks/abc/jupyter')

    def test_conflict_prevents_any_editor_mutation(self):
        engine = Engine()
        engine.wait_snapshot = lambda **kw: {'url': 'https://www.educoder.net/tasks/abc',
            'current_path': 'main.py', 'content': 'changed in browser'}
        engine.store.checkpoint = lambda tid: {'files': {'main.py': {'sha256': digest('old')}}}
        with patch.object(engine, 'js') as js:
            with self.assertRaises(ToolError) as error:
                engine.write_code('memory-only replacement')
            self.assertEqual(error.exception.code, 'code_conflict')
            js.assert_not_called()

    def test_wrong_task_prevents_any_editor_mutation(self):
        engine = Engine()
        engine.wait_snapshot = lambda **kw: {'url': 'https://www.educoder.net/tasks/new', 'current_path': 'a', 'content': 'x'}
        with patch.object(engine, 'js') as js:
            with self.assertRaises(ToolError) as error:
                engine.write_code('memory-only replacement', task='old')
            self.assertEqual(error.exception.code, 'task_mismatch')
            js.assert_not_called()

    def test_new_build_cannot_reuse_old_passed_dom_result(self):
        engine = Engine()
        snap = {'url': 'https://www.educoder.net/tasks/abc', 'game_status': 2, 'evaluating': False}
        engine.wait_snapshot = lambda **kw: snap
        engine.snapshot = lambda: snap
        engine.store.checkpoint = lambda tid: {}
        engine.collect = lambda: None
        engine.sleep = lambda _: None
        engine.events.append({'seq': 1, 'path': '/api/tasks/abc/game_build.json', 'data': {'status': 1}})
        engine.start_evaluation = Mock(return_value=0)
        engine.js = Mock(return_value={})
        with patch('educoder_agent.core.write_json'), patch('educoder_agent.core.time.monotonic', side_effect=[0, 0, 2]):
            with self.assertRaises(ToolError) as error:
                engine.evaluate(timeout=1)
            self.assertEqual(error.exception.code, 'evaluation_timeout')


class TestEvaluationReady(unittest.TestCase):
    def setUp(self):
        self.engine = Engine()
        self.engine.collect = Mock()
        self.engine.sleep = Mock()
        self.snap = {'url': 'https://www.educoder.net/tasks/fixture', 'current_path': 'remote/file'}

    def test_missing_then_disabled_control_waits_and_clicks_only_once(self):
        self.engine.js = Mock(side_effect=[
            {'clicked': False, 'reason': 'missing'},
            {'clicked': False, 'reason': 'disabled'},
            {'clicked': True, 'task_id': 'fixture'}])
        baseline = self.engine.start_evaluation(self.snap)
        self.assertEqual(baseline, 0)
        self.assertEqual(self.engine.sleep.call_count, 2)
        self.assertEqual(self.engine.js.call_count, 3)
        self.assertEqual(self.engine.js.call_args.args, ('evaluate_click',))

    def test_task_change_or_existing_build_prevents_click_retry(self):
        for reason, code in (('task_mismatch', 'task_mismatch'), ('evaluating', 'evaluation_running')):
            self.engine.js = Mock(return_value={'clicked': False, 'reason': reason})
            with self.assertRaises(ToolError) as error:
                self.engine.start_evaluation(self.snap)
            self.assertEqual(error.exception.code, code)
            self.engine.js.assert_called_once()

    def test_uncertain_click_never_retries(self):
        self.engine.js = Mock(side_effect=RuntimeError('synthetic transport failure'))
        with self.assertRaises(ToolError) as error:
            self.engine.start_evaluation(self.snap)
        self.assertEqual(error.exception.code, 'evaluation_click_uncertain')
        self.engine.js.assert_called_once()

    def test_invalid_receipt_never_retries(self):
        self.engine.js = Mock(return_value={})
        with self.assertRaises(ToolError) as error:
            self.engine.start_evaluation(self.snap)
        self.assertEqual(error.exception.code, 'evaluation_click_uncertain')
        self.engine.js.assert_called_once()

    def test_readiness_timeout_reports_state_without_recording_submission(self):
        self.engine.wait_snapshot = Mock(return_value=self.snap)
        self.engine.check_selection = Mock()
        self.engine.store.checkpoint = Mock(return_value={})
        self.engine.js = Mock(return_value={'clicked': False, 'reason': 'disabled', 'enabled': 0})
        with patch('educoder_agent.core.time.monotonic', side_effect=[0, 0, 46]), patch('educoder_agent.core.write_json') as write:
            with self.assertRaises(ToolError) as error:
                self.engine.evaluate()
        self.assertEqual(error.exception.code, 'evaluation_button_timeout')
        self.assertEqual(error.exception.details['control']['reason'], 'disabled')
        write.assert_not_called()

    def test_baseline_excludes_results_collected_while_waiting(self):
        def collect():
            self.engine.seq += 1
        self.engine.collect.side_effect = collect
        self.engine.js = Mock(side_effect=[{'clicked': False, 'reason': 'missing'}, {'clicked': True}])
        self.assertEqual(self.engine.start_evaluation(self.snap), 2)


class TestSaveFlow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=BASE / '.state')
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine()
        self.engine.store = SnapshotStore(self.temp.name)
        self.engine.browser = Mock()
        self.engine.collect = Mock()
        self.engine.sleep = Mock()
        self.engine.check_selection = Mock()
        self.initial = {'url': 'https://www.educoder.net/tasks/fixture',
                        'current_path': 'remote/file', 'content': 'synthetic initial value'}
        self.engine.store.context(self.initial)
        self.engine.select_file = Mock(return_value=self.initial)

    def test_callback_without_status_evaluates_without_refresh(self):
        fresh = dict(self.initial, content='synthetic replacement')
        self.engine.select_file.side_effect = [self.initial, fresh]
        self.engine.js = Mock(return_value={'callback': True, 'rejected': False, 'content_matches': True})
        self.engine.evaluate = Mock(return_value={'result': {'passed': False}})
        result = self.engine.input_code('synthetic replacement', 'fixture', 'remote/file')
        self.assertEqual(result['action'], 'revise')
        self.engine.browser.refresh.assert_not_called()
        self.engine.evaluate.assert_called_once()
        self.assertEqual(self.engine.store.checkpoint('fixture')['files']['remote/file']['sha256'],
                         digest('synthetic replacement'))

    def test_unchanged_editor_is_saved_then_evaluated_without_refresh(self):
        self.engine.js = Mock(return_value={'callback': True, 'content_matches': None, 'rejected': False})
        self.engine.evaluate = Mock(return_value={'result': {'passed': True}})
        result = self.engine.input_code(self.initial['content'], 'fixture', 'remote/file', advance=False)
        self.assertEqual(result['action'], 'passed')
        self.engine.js.assert_called_once_with('save', selector='')
        self.engine.evaluate.assert_called_once()
        self.engine.browser.refresh.assert_not_called()

    def test_webpage_normalization_does_not_block_platform_evaluation(self):
        self.engine.js = Mock(return_value={'callback': True, 'content_matches': False})
        actual = 'synthetic replacement\r\n'
        self.engine.select_file.side_effect = [self.initial, dict(self.initial, content=actual)]
        self.engine.evaluate = Mock(return_value={'result': {'passed': False}})
        result = self.engine.input_code('synthetic replacement\n', 'fixture', 'remote/file')
        self.assertEqual(result['action'], 'revise')
        self.engine.evaluate.assert_called_once()
        self.engine.browser.refresh.assert_not_called()
        self.assertEqual(self.engine.store.checkpoint('fixture')['files']['remote/file']['sha256'],
                         digest(actual))
        self.assertEqual(result['sha256'], digest(actual))

    def test_explicit_save_rejection_still_blocks_evaluation(self):
        self.engine.js = Mock(return_value={'callback': True, 'rejected': True})
        self.engine.evaluate = Mock()
        with self.assertRaises(ToolError) as error:
            self.engine.input_code('synthetic replacement', 'fixture', 'remote/file')
        self.assertEqual(error.exception.code, 'save_rejected')
        self.engine.evaluate.assert_not_called()
        self.engine.browser.refresh.assert_not_called()

    def test_task_change_during_save_still_blocks_evaluation(self):
        self.engine.js = Mock(return_value={'callback': True})
        self.engine.select_file.side_effect = [self.initial, dict(self.initial, url='https://www.educoder.net/tasks/other')]
        self.engine.evaluate = Mock()
        with self.assertRaises(ToolError) as error:
            self.engine.input_code('synthetic replacement', 'fixture', 'remote/file')
        self.assertEqual(error.exception.code, 'task_mismatch')
        self.engine.evaluate.assert_not_called()

    def test_button_save_accepts_actual_schema_without_exposing_content_or_key(self):
        self.engine.js = Mock(return_value=Mock())
        def collect():
            if self.engine.collect.call_count > 1:
                self.engine.events.append({'seq': 1, 'path': '/api/myshixuns/f/update_file.json',
                                          'data': {'content': 'synthetic replacement', 'sec_key': 'private'}})
        self.engine.collect.side_effect = collect
        self.engine.save()

    def test_button_rejection_never_exposes_raw_response(self):
        self.engine.js = Mock(return_value=Mock())
        def collect():
            if self.engine.collect.call_count > 1:
                self.engine.events.append({'seq': 1, 'path': '/api/myshixuns/f/update_file.json',
                                          'data': {'status': -1, 'content': 'private content', 'sec_key': 'private'}})
        self.engine.collect.side_effect = collect
        with self.assertRaises(ToolError) as error:
            self.engine.save()
        self.assertEqual(error.exception.code, 'save_rejected')
        self.assertEqual(error.exception.details, {})


class TestNextLevel(unittest.TestCase):
    def setUp(self):
        self.engine = Engine()
        self.url = 'https://www.educoder.net/tasks/C/H/current'
        self.next_url = 'https://www.educoder.net/tasks/C/H/following'
        self.snap = {'url': self.url, 'game_status': 2, 'next_task': 'following'}
        self.engine.wait_snapshot = Mock(return_value=self.snap)
        self.engine.check_selection = Mock()
        self.engine.navigate = Mock()
        self.engine.context = Mock(return_value={'task_id': 'following', 'url': self.next_url})
        self.engine.js = Mock(return_value='/tasks/following')

    def test_explicit_next_id_navigates_without_clicking_overlay(self):
        result = self.engine.next_level()
        self.assertEqual(result['task_id'], 'following')
        self.engine.navigate.assert_called_once_with(self.next_url)
        self.engine.js.assert_not_called()

    def test_short_webpage_link_preserves_course_and_homework(self):
        self.snap['next_task'] = None
        self.engine.next_level()
        self.engine.js.assert_called_once_with('task_next_url')
        self.engine.navigate.assert_called_once_with(self.next_url)

    def test_link_to_other_experiment_or_site_is_rejected(self):
        self.snap['next_task'] = None
        for destination in ('/tasks/C/other/following', 'https://evil.test/tasks/following'):
            self.engine.js.return_value = destination
            with self.assertRaises(ToolError):
                self.engine.next_level()
        self.engine.navigate.assert_not_called()

    def test_unpassed_task_cannot_advance(self):
        self.snap['game_status'] = 0
        with self.assertRaises(ToolError) as error:
            self.engine.next_level()
        self.assertEqual(error.exception.code, 'current_not_passed')
        self.engine.navigate.assert_not_called()

    def test_last_level_is_distinguished_from_missing_navigation(self):
        self.snap.update(next_task=None, is_last_game=True)
        self.engine.js.return_value = None
        with self.assertRaises(ToolError) as error:
            self.engine.next_level()
        self.assertEqual(error.exception.code, 'next_level_missing')

    def test_wrong_task_after_redirect_is_not_accepted(self):
        self.engine.context.return_value = {'task_id': 'current', 'url': self.url}
        with self.assertRaises(ToolError) as error:
            self.engine.next_level()
        self.assertEqual(error.exception.code, 'next_level_unavailable')

    def test_navigation_failure_preserves_passed_evaluation(self):
        self.engine.write_code = Mock(return_value={'saved': True})
        self.engine.evaluate = Mock(return_value={'result': {'passed': True}})
        self.engine.navigate.side_effect = RuntimeError('synthetic driver failure')
        result = self.engine.input_code('synthetic memory value', 'current', 'remote/file')
        self.assertEqual(result['action'], 'passed_navigation_pending')
        self.assertTrue(result['evaluation']['result']['passed'])
        self.assertNotIn('synthetic driver failure', result['navigation_error'])
        self.engine.evaluate.assert_called_once()


class TestInputLoop(unittest.TestCase):
    def test_json_preserves_newlines_and_unicode_in_memory(self):
        data = parse_input(json.dumps({'task_id': 'abc', 'path': 'src/file', 'code': '第一行\n第二行\n'}))
        self.assertEqual(data['code'], '第一行\n第二行\n')
        self.assertEqual(data['task'], 'abc')

    def test_input_rejects_missing_identity_and_local_source_path(self):
        for data in ({'code': ''}, {'task_id': 'abc', 'path': 'f', 'source': 'file.py'}):
            with self.assertRaises(ToolError):
                parse_input(json.dumps(data))

    def test_failed_evaluation_returns_to_agent_without_advancing(self):
        engine = Engine()
        with patch.object(engine, 'write_code', return_value={'saved': True}), patch.object(engine, 'evaluate', return_value={'result': {'passed': False}}), patch.object(engine, 'next_level') as next_level:
            result = engine.input_code('memory-only', 'abc', 'remote/path')
            self.assertEqual(result['action'], 'revise')
            next_level.assert_not_called()

    def test_passed_evaluation_returns_next_context(self):
        engine = Engine()
        with patch.object(engine, 'write_code', return_value={'saved': True}), patch.object(engine, 'evaluate', return_value={'result': {'passed': True}}), patch.object(engine, 'next_level', return_value={'task_id': 'next'}):
            result = engine.input_code('memory-only', 'abc', 'remote/path')
            self.assertEqual(result['action'], 'next_level')
            self.assertEqual(result['context']['task_id'], 'next')

    def test_completed_experiment_moves_to_next_experiment(self):
        engine = Engine()
        with patch.object(engine, 'write_code', return_value={'saved': True}), patch.object(engine, 'evaluate', return_value={'result': {'passed': True}}), patch.object(engine, 'next_level', side_effect=ToolError('end', 'next_level_missing')), patch.object(engine, 'next_experiment', return_value={'context': {'task_id': 'next-exp'}}):
            result = engine.input_code('memory-only', 'abc', 'remote/path')
            self.assertEqual(result['action'], 'next_experiment')

    def test_failed_save_never_runs_evaluation(self):
        engine = Engine()
        with patch.object(engine, 'write_code', side_effect=ToolError('save failed')), patch.object(engine, 'evaluate') as evaluate:
            with self.assertRaises(ToolError):
                engine.input_code('memory-only', 'abc', 'remote/path')
            evaluate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
