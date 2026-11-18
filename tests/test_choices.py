"""Choice tasks, fresh evaluation receipts and progress, using synthetic webpage data."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from educoder_agent.core import BASE, DEFAULT_COURSE, Engine, ToolError, choice_result, compact_result, load_json, write_json
from educoder_agent.codex_solver import validate_answer, validate_choices
from educoder_agent.cli import parse_input
from educoder_agent.paths import ProjectPaths


QUESTIONS = [{'id': 'single', 'subject': 'fixture question', 'multiple': False,
              'options': [{'position': 0, 'text': 'fixture a'}, {'position': 1, 'text': 'fixture b'}]},
             {'id': 'multi', 'multiple': True, 'options': [{'position': 2}, {'position': 4}]}]
ANSWERS = [{'question_id': 'single', 'positions': [1]}, {'question_id': 'multi', 'positions': [2, 4]}]


class TestChoiceValidation(unittest.TestCase):
    def test_choice_schema_and_cli_transport(self):
        value = {'action': 'choose', 'files': [], 'read_files': [], 'answers': ANSWERS}
        self.assertEqual(validate_answer(value, {'task_type': 'choice', 'choices': QUESTIONS}), value)
        parsed = parse_input(json.dumps({'task_id': 'fixture', 'answers': ANSWERS, 'advance': False}))
        self.assertEqual(parsed, {'task': 'fixture', 'answers': ANSWERS, 'advance': False})
        schema = load_json(ProjectPaths().solution_schema)
        self.assertIn('choose', schema['properties']['action']['enum'])
        self.assertIn('answers', schema['required'])

    def test_invalid_choices_never_reach_page(self):
        for answers in ([], ANSWERS[:1], [ANSWERS[0], ANSWERS[0]],
                        [dict(ANSWERS[0], question_id='unknown'), ANSWERS[1]],
                        [dict(ANSWERS[0], positions=[True]), ANSWERS[1]],
                        [dict(ANSWERS[0], positions=[0, 1]), ANSWERS[1]],
                        [ANSWERS[0], dict(ANSWERS[1], positions=[2, 2])],
                        [ANSWERS[0], dict(ANSWERS[1], positions=[3])]):
            with self.subTest(answers=answers), self.assertRaises(ToolError):
                validate_choices(answers, QUESTIONS)
        with self.assertRaises(ToolError):
            validate_answer({'action': 'choose', 'files': [], 'read_files': [], 'answers': ANSWERS}, {})
        for data in ({'task_id': 'fixture', 'answers': ANSWERS, 'code': ''},
                     {'task_id': 'fixture', 'answers': ANSWERS, 'evaluate': 'false'}):
            with self.assertRaises(ToolError):
                parse_input(json.dumps(data))

    def test_hidden_grade_is_submitted_without_claiming_correctness(self):
        data = {'had_all_submmit': True, 'challenge_chooses_count': 1, 'choose_correct_num': None,
                'test_sets': [{'result': None, 'actual_output': 'A'}]}
        result = compact_result(choice_result(data))
        self.assertTrue(result['submitted'])
        self.assertTrue(result['passed'])  # platform permits moving forward
        self.assertFalse(result['feedback_visible'])
        self.assertNotIn('correct', result)
        self.assertIn('未公开判分', result['message'])
        large = dict(data, test_sets=[{'result': None, 'actual_output': 'fixture' * 1000}])
        self.assertFalse(compact_result(choice_result(large), 100)['feedback_visible'])
        data['choose_correct_num'] = 0
        self.assertFalse(choice_result(data)['status'] == 2)
        data['choose_correct_num'] = 1
        self.assertTrue(choice_result(data)['correct'])
        self.assertIsNone(choice_result({'status': 0}))


class TestChoiceEngine(unittest.TestCase):
    def setUp(self):
        test_root = BASE / '.state/tests'
        test_root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=test_root)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        paths = ProjectPaths(self.base)
        paths.browser_script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ProjectPaths().browser_script, paths.browser_script)
        write_json(paths.config, {'course_url': DEFAULT_COURSE})
        self.engine = Engine(self.base)
        self.snap = {'url': 'https://www.educoder.net/tasks/J2F8SUKL/4115215/fixture',
                     'task_id': 'fixture', 'task_type': 'choice', 'choices': QUESTIONS,
                     'description': '', 'subject': 'fixture level', 'game_status': 0,
                     'experiment': {'homework_id': '4115215', 'position': 1, 'total': 2},
                     'editor_available': False, 'choice_ready': True, 'evaluating': False}
        write_json(self.engine.root / 'course.json', {'course_url': DEFAULT_COURSE, 'experiments': [
            {'id': '4115215', 'finished': 0, 'total': 2, 'complete': False}], 'counted_tasks': []})

    def test_no_editor_needed_and_no_question_or_answer_persisted(self):
        self.engine.snapshot = Mock(return_value=self.snap)
        self.engine.js = Mock()
        ready = self.engine.wait_snapshot(code=True)
        context = self.engine.context_from_snapshot(ready)
        self.assertEqual(context['task_type'], 'choice')
        self.assertEqual(context['choices'], QUESTIONS)
        self.assertIsNone(context['current_file'])
        self.assertNotIn('notice', context)
        self.engine.js.assert_not_called()
        for path in self.engine.root.rglob('*.json'):
            self.assertNotIn('fixture question', path.read_text(encoding='utf-8'))
            self.assertNotIn('fixture a', path.read_text(encoding='utf-8'))

    def test_choices_wait_for_controls_then_apply_and_evaluate(self):
        self.engine.snapshot = Mock(side_effect=[dict(self.snap, choice_ready=False), self.snap])
        self.engine.sleep = Mock()
        self.engine.js = Mock(return_value={'selected': True})
        self.engine.evaluate = Mock(return_value={'result': {'passed': True}})
        result = self.engine.input_choices(ANSWERS, 'fixture', advance=False)
        self.assertEqual(result['action'], 'passed')
        self.engine.js.assert_called_once_with('set_choices', task_id='fixture', answers=ANSWERS)
        self.engine.evaluate.assert_called_once()

    def test_evaluation_requires_fresh_choice_receipt_and_handles_wrong_answer(self):
        self.engine.wait_snapshot = Mock(return_value=self.snap)
        self.engine.snapshot = Mock(return_value=dict(self.snap, game_status=2))
        self.engine.sleep = Mock()
        self.engine.start_evaluation = Mock(return_value=1)
        self.engine.collect = Mock()
        self.engine.events.extend([
            {'seq': 1, 'path': '/api/tasks/fixture/choose_build.json', 'data': {
                'had_all_submmit': True, 'choose_correct_num': 2, 'challenge_chooses_count': 2}},
            {'seq': 2, 'path': '/api/tasks/fixture/choose_build.json', 'data': {
                'had_all_submmit': True, 'choose_correct_num': 1, 'challenge_chooses_count': 2,
                'test_sets': [{'result': False, 'actual_output': 'A'}]}}])
        self.engine.context_from_snapshot(self.snap)
        with patch('educoder_agent.diagnostics.record_error', return_value={}):
            result = self.engine.evaluate(timeout=1)
        self.assertFalse(result['result']['passed'])
        self.assertEqual(result['result']['failed_cases'][0]['actual_output'], 'A')

    def test_count_updates_once_per_level_even_after_restart(self):
        self.engine.context_from_snapshot(self.snap)
        self.engine.finish_result('fixture', {'status': 2})
        self.assertEqual(self.engine.progress_snapshot()['4115215']['finished'], 1)
        self.engine.finish_result('fixture', {'status': 2})
        restarted = Engine(self.base)
        restarted.finish_result('fixture', {'status': 2})
        self.assertEqual(restarted.progress_snapshot()['4115215']['finished'], 1)
        second = dict(self.snap, task_id='second', url=self.snap['url'].replace('/fixture', '/second'))
        restarted.context_from_snapshot(second)
        restarted.finish_result('second', {'status': 2})
        self.assertEqual(restarted.progress_snapshot()['4115215']['finished'], 2)
        self.assertTrue(restarted.progress_snapshot()['4115215']['complete'])

    def test_final_level_ignores_unrelated_fallback_link_and_opens_selected_choice(self):
        self.engine.wait_snapshot = Mock(return_value=dict(self.snap, game_status=2, is_last_game=True))
        self.engine.js = Mock()
        with self.assertRaises(ToolError) as error:
            self.engine.next_level()
        self.assertEqual(error.exception.code, 'next_level_missing')
        self.engine.js.assert_not_called()
        following = {'task_type': 'choice', 'choices': QUESTIONS}
        self.engine.next_experiment = Mock(return_value={'context': following})
        result = self.engine.after_evaluation({}, {'result': {'passed': True}}, True)
        self.assertEqual(result['action'], 'next_experiment')
        self.assertEqual(result['context'], following)

    def test_navigation_waits_for_react_to_apply_confirmed_submission(self):
        self.engine.context_from_snapshot(self.snap)
        self.engine.finish_result('fixture', {'status': 2})
        self.engine.wait_snapshot = Mock(return_value=self.snap)
        self.engine.snapshot = Mock(return_value=dict(self.snap, game_status=2, is_last_game=True))
        self.engine.sleep = Mock()
        with self.assertRaises(ToolError) as error:
            self.engine.next_level()
        self.assertEqual(error.exception.code, 'next_level_missing')
        self.engine.sleep.assert_called_once_with(0.15)

    def test_hidden_choice_submission_remains_honest_on_reopen(self):
        self.engine.context_from_snapshot(dict(self.snap, game_status=2, feedback_visible=False))
        self.assertEqual(self.engine.progress_snapshot()['4115215']['level_status'], '已提交（未公开判分）')

    def test_collector_accepts_actual_choice_build_endpoint(self):
        path = '/api/tasks/fixture/choose_build.json'
        request = {'method': 'Network.requestWillBeSent', 'params': {'requestId': 'r', 'request': {
            'url': 'https://data.educoder.net' + path, 'postData': '{}'}}}
        done = {'method': 'Network.loadingFinished', 'params': {'requestId': 'r'}}
        self.engine.browser = Mock()
        self.engine.browser.get_log.return_value = [{'message': json.dumps({'message': event})} for event in (request, done)]
        self.engine.browser.execute_cdp_cmd.return_value = {'body': json.dumps({'had_all_submmit': True})}
        self.engine.collect()
        self.assertEqual(self.engine.events[-1]['path'], path)
        self.assertTrue(self.engine.events[-1]['data']['had_all_submmit'])


if __name__ == '__main__':
    unittest.main()
