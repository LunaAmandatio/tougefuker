"""Selection boundaries and persistence; no browser or lab code execution."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from educoder_agent.core import BASE, Engine, SnapshotStore, ToolError, load_json, write_json
from educoder_agent.cli import parser


class TestSelection(unittest.TestCase):
    def setUp(self):
        parent = BASE / '.state' / 'tests'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.engine = Engine()
        self.engine.root = Path(self.temp.name)
        self.engine.store = SnapshotStore(self.engine.root)
        self.items = [
            {'id': '4115214', 'title': 'A', 'complete': False, 'can_enter': True},
            {'id': '4115215', 'title': 'B', 'complete': False, 'can_enter': True},
            {'id': '4115216', 'title': 'C', 'complete': True, 'can_enter': True}]
        write_json(self.engine.root / 'course.json', {
            'course_url': self.engine.config['course_url'], 'experiments': self.items})

    def test_selection_persists_and_filters_candidates_in_page_order(self):
        self.engine.selection(['4115216', '4115215', '4115215'])
        self.assertEqual(self.engine.selection(), {'restricted': True, 'ids': ['4115216', '4115215']})
        self.assertEqual([c['id'] for c in self.engine.experiment_candidates(self.items)], ['4115215'])
        self.engine.selection(['4115215', '4115214'])
        self.assertEqual([c['id'] for c in self.engine.experiment_candidates(self.items)], ['4115214', '4115215'])

    def test_empty_selection_stops_and_unrestricted_restores_previous_behavior(self):
        self.engine.selection([])
        with self.assertRaises(ToolError) as error:
            self.engine.experiment_candidates(self.items)
        self.assertEqual(error.exception.code, 'empty_selection')
        self.engine.selection(unrestricted=True)
        self.assertEqual(len(self.engine.experiment_candidates(self.items)), 2)

    def test_completed_selection_stops_without_entering_other_unfinished_items(self):
        self.engine.selection(['4115216'])
        with self.assertRaises(ToolError) as error:
            self.engine.experiment_candidates(self.items)
        self.assertEqual(error.exception.code, 'selection_complete')

    def test_unknown_or_unavailable_selected_items_are_not_reported_complete(self):
        self.engine.selection(['4115215'])
        for items in ([dict(self.items[1], complete=None)], [dict(self.items[1], can_enter=False)], []):
            with self.subTest(items=items), self.assertRaises(ToolError) as error:
                self.engine.experiment_candidates(items)
            self.assertEqual(error.exception.code, 'no_next_experiment')

    def test_unknown_ids_cannot_replace_applied_selection(self):
        self.engine.selection(['4115214'])
        for ids in (['999'], '4115214', [4115214]):
            with self.assertRaises(ToolError):
                self.engine.selection(ids)
        self.assertEqual(self.engine.selection()['ids'], ['4115214'])

    def test_selection_is_scoped_to_course(self):
        self.engine.selection(['4115214'])
        self.engine.config = dict(self.engine.config, course_url='https://www.educoder.net/classrooms/other/shixun_homework')
        self.assertFalse(self.engine.selection()['restricted'])
        with self.assertRaises(ToolError) as error:
            self.engine.selection(['4115214'])
        self.assertEqual(error.exception.code, 'selection_needs_list')

    def test_unselected_lab_cannot_be_explicitly_entered_written_or_evaluated(self):
        self.engine.selection(['4115214'])
        snapshot = {'url': 'https://www.educoder.net/tasks/J2F8SUKL/4115215/fixture'}
        with self.assertRaises(ToolError):
            self.engine.experiment_candidates(self.items, '4115215')
        with patch.object(self.engine, 'wait_snapshot', return_value=snapshot), patch.object(self.engine, 'js') as js:
            for operation in (lambda: self.engine.write_code('synthetic memory value'), self.engine.evaluate):
                with self.assertRaises(ToolError) as error:
                    operation()
                self.assertEqual(error.exception.code, 'experiment_not_selected')
            js.assert_not_called()
        self.engine.check_selection('https://www.educoder.net/tasks/J2F8SUKL/4115214/fixture')

    def test_success_loop_reports_selection_complete(self):
        self.engine.selection(['4115216'])
        with patch.object(self.engine, 'write_code', return_value={'saved': True}), \
                patch.object(self.engine, 'evaluate', return_value={'result': {'passed': True}}), \
                patch.object(self.engine, 'next_level', side_effect=ToolError('end', 'next_level_missing')), \
                patch.object(self.engine, 'next_experiment', side_effect=ToolError('done', 'selection_complete')):
            result = self.engine.input_code('synthetic memory value', 'fixture', 'remote')
        self.assertEqual(result['action'], 'selection_complete')

    def test_cli_distinguishes_read_clear_and_select(self):
        self.assertIsNone(parser().parse_args(['select']).ids)
        self.assertEqual(parser().parse_args(['select', '--ids']).ids, [])
        self.assertEqual(parser().parse_args(['select', '--ids', '4115214', '--start']).ids, ['4115214'])

    def test_auto_context_reuses_selected_current_task(self):
        from types import SimpleNamespace
        self.engine.selection(['4115214'])
        self.engine.browser = SimpleNamespace(current_url='https://www.educoder.net/tasks/J2F8SUKL/4115214/fixture')
        with patch.object(self.engine, 'wait_snapshot', return_value={'game_status': 0}), \
                patch.object(self.engine, 'context', return_value={'task_id': 'fixture'}), \
                patch.object(self.engine, 'next_experiment') as next_experiment:
            self.assertEqual(self.engine.auto_context()['task_id'], 'fixture')
            next_experiment.assert_not_called()

    def test_ready_snapshot_is_reused_without_reading_page_again(self):
        from types import SimpleNamespace
        self.engine.selection(['4115214'])
        url = 'https://www.educoder.net/tasks/J2F8SUKL/4115214/fixture'
        self.engine.browser = SimpleNamespace(current_url=url)
        snap = {'url': url, 'game_status': 0, 'editor_available': True,
                'description': 'synthetic task description', 'current_path': 'remote/file',
                'content': 'synthetic editor content'}
        with patch.object(self.engine, 'wait_snapshot', return_value=snap) as read, \
                patch.object(self.engine, 'context') as context:
            self.assertEqual(self.engine.auto_context()['code'], 'synthetic editor content')
            read.assert_called_once()
            context.assert_not_called()

    def test_auto_context_does_not_start_from_unselected_current_task(self):
        from types import SimpleNamespace
        self.engine.selection(['4115214'])
        self.engine.browser = SimpleNamespace(current_url='https://www.educoder.net/tasks/J2F8SUKL/4115215/other')
        with patch.object(self.engine, 'next_experiment', return_value={'context': {'task_id': 'selected'}}) as next_experiment:
            self.assertEqual(self.engine.auto_context()['task_id'], 'selected')
            next_experiment.assert_called_once()

    def test_restarted_browser_skips_passed_entry_before_calling_model(self):
        self.engine.selection(['4115214'])
        self.engine.browser = None
        with patch.object(self.engine, 'next_experiment', return_value={'context': {
                'task_id': 'passed', 'game_status': 2}}), \
                patch.object(self.engine, 'next_level', return_value={
                    'task_id': 'unfinished', 'game_status': 0}) as next_level:
            self.assertEqual(self.engine.auto_context()['task_id'], 'unfinished')
            next_level.assert_called_once()

    def test_resume_skips_multiple_passed_levels(self):
        self.engine.selection(['4115214'])
        self.engine.browser = None
        with patch.object(self.engine, 'next_experiment', return_value={'context': {
                'task_id': 'passed', 'game_status': 2}}), \
                patch.object(self.engine, 'next_level', side_effect=[
                    {'task_id': 'also-passed', 'game_status': 2},
                    {'task_id': 'unfinished', 'game_status': 0}]):
            self.assertEqual(self.engine.auto_context()['task_id'], 'unfinished')

    def test_repeated_passed_entry_stops_without_resubmitting(self):
        self.engine.selection(['4115214'])
        self.engine.browser = None
        with patch.object(self.engine, 'next_experiment', return_value={'context': {
                'task_id': 'passed', 'game_status': 2}}), \
                patch.object(self.engine, 'next_level', return_value={'task_id': 'passed', 'game_status': 2}):
            with self.assertRaises(ToolError) as error:
                self.engine.auto_context()
            self.assertEqual(error.exception.code, 'passed_navigation_pending')


if __name__ == '__main__':
    unittest.main()
