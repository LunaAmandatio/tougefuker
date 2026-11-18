"""Exercise actual Tk widgets without launching a browser or service."""
import sys
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gui import App
from core import BASE, load_json


class TestSelectionGUI(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        directory = BASE / '.state' / 'tests'
        directory.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.temp.cleanup)
        self.model_settings = Path(self.temp.name) / 'ui.json'
        settings_patch = patch('gui.MODEL_SETTINGS', self.model_settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.app = App(self.root)
        self.items = [
            {'id': '4115214', 'title': '实验一', 'complete': False, 'finished': 0, 'total': 7,
             'status': ['提交中'], 'can_enter': True},
            {'id': '4115215', 'title': '实验二', 'complete': True, 'finished': 5, 'total': 5,
             'status': ['已归档'], 'can_enter': True},
            {'id': '4115216', 'title': '实验三', 'complete': None, 'can_enter': False}]
        self.app.show_experiments(self.items, {'restricted': False, 'ids': []})

    def test_titles_status_and_keyboard_checkbox_payload(self):
        self.assertEqual(self.app.table.item('4115214', 'values'), ('实验一', '未完成', '0/7', '提交中', '是', ''))
        self.assertEqual(self.app.table.item('4115215', 'values')[1], '已完成')
        self.app.table.focus('4115214')
        self.app.toggle_space(None)
        self.assertEqual(self.app.table.item('4115214', 'text'), '☑')
        with patch.object(self.app, 'call') as call:
            self.app.apply_selection(start=True)
            call.assert_called_once_with('select', {'ids': ['4115214'], 'start': True})

    def test_refresh_preserves_unapplied_checkboxes_and_saved_selection_restores(self):
        self.app.toggle_row('4115214')
        self.app.show_experiments(self.items, {'restricted': True, 'ids': ['4115215']})
        self.assertEqual(self.app.checked, {'4115214'})
        self.app.selection_dirty = False
        self.app.show_experiments(self.items, {'restricted': True, 'ids': ['4115215']})
        self.assertEqual(self.app.checked, {'4115215'})

    def test_bulk_selection_and_empty_start(self):
        self.app.check_many('all')
        self.assertEqual(len(self.app.checked), 3)
        self.app.check_many('unfinished')
        self.assertEqual(self.app.checked, {'4115214'})
        self.app.check_many('none')
        with patch.object(self.app, 'call') as call:
            self.app.apply_selection(start=True)
            call.assert_not_called()
            self.app.apply_selection()
            call.assert_called_once_with('select', {'ids': [], 'start': False})

    def test_codex_start_uses_selected_ids_and_model_and_outputs_are_separate(self):
        self.app.toggle_row('4115214')
        self.app.model.set('requested-model')
        with patch.object(self.app, 'call') as call:
            self.app.start_auto()
            call.assert_called_once_with('auto-start', {'ids': ['4115214'], 'model': 'requested-model'})
        result = {'automation': {'running': True, 'message': 'thinking'}, 'cursor': 1,
                  'events': [{'seq': 1, 'kind': 'model', 'text': 'synthetic model output'}]}
        self.app.show_automation(result)
        self.app.show_automation(result)
        self.assertEqual(self.app.model_output.get('1.0', 'end').count('synthetic model output'), 1)
        self.assertNotIn('synthetic model output', self.app.output.get('1.0', 'end'))
        self.assertEqual(len(self.app.output_panes.panes()), 2)

    def test_four_buttons_and_pause_remains_available(self):
        def buttons(widget):
            result = []
            for child in widget.winfo_children():
                if child.winfo_class() == 'TButton':
                    result.append(child)
                result.extend(buttons(child))
            return result
        self.assertEqual([b.cget('text') for b in buttons(self.root)], ['登录', '获取题目列表', '自动答题', '暂停'])
        self.app.busy = self.app.auto_running = True
        with patch.object(self.app, 'call') as call:
            self.app.pause_button.invoke()
            call.assert_called_once_with('pause', interrupt=True)
        self.assertNotIn('disabled', self.app.pause_button.state())

    def test_live_progress_after_each_level_and_experiment_preserves_selection(self):
        self.app.toggle_row('4115214')
        live = {'4115214': {'task_id': 'fixture', 'finished': 1, 'total': 7, 'complete': False,
                           'subject': '关卡一', 'level_status': '已通过'}}
        state = {'running': True, 'homework_id': '4115214', 'task_id': 'fixture', 'phase': 'submitting'}
        self.app.show_automation({'automation': state, 'experiments': live})
        self.assertEqual(self.app.table.set('4115214', 'progress'), '1/7')
        self.assertIn('关卡一 · 已通过', self.app.table.set('4115214', 'level'))
        live['4115214'].update(finished=7, complete=True)
        self.app.show_automation({'automation': state, 'experiments': live})
        self.assertEqual(self.app.table.set('4115214', 'state'), '已完成')
        self.assertEqual(self.app.checked, {'4115214'})
        self.assertEqual(self.app.table.item('4115214', 'text'), '☑')

    def test_progress_received_before_list_is_applied_when_list_arrives(self):
        self.app.experiments = []
        self.app.show_automation({'automation': {'running': False}, 'experiments': {
            '4115214': {'finished': 2, 'total': 7, 'subject': '关卡二', 'level_status': '已通过'}}})
        self.app.show_experiments(self.items, {'ids': []})
        self.assertEqual(self.app.table.set('4115214', 'progress'), '2/7')

    def test_model_can_be_typed_saved_and_restored_without_extra_buttons(self):
        self.app.toggle_row('4115214')
        self.app.model_entry.set('  custom/new-model  ')
        with patch.object(self.app, 'call') as call:
            self.app.start_auto()
            call.assert_called_once_with('auto-start', {'model': 'custom/new-model', 'ids': ['4115214']})
        self.assertEqual(load_json(self.model_settings)['model'], 'custom/new-model')
        reopened = App(self.root)
        self.assertEqual(reopened.model.get(), 'custom/new-model')
        self.assertIn('custom/new-model', reopened.model_entry.cget('values'))

    def test_invalid_model_never_starts_or_silently_uses_default(self):
        self.app.toggle_row('4115214')
        for value in ('', 'two models', 'model\nextra'):
            self.app.model.set(value)
            with patch.object(self.app, 'call') as call:
                self.app.start_auto()
                call.assert_not_called()
            self.assertIn('invalid_model', self.app.status.get())
        self.assertFalse(self.model_settings.exists())

    def test_model_edit_remains_available_during_run_and_failure_shows_code(self):
        self.app.show_automation({'automation': {'running': True, 'model': 'active-model', 'message': 'thinking'}})
        self.assertNotIn('disabled', self.app.model_entry.state())
        self.app.model_entry.set('next-model')
        with patch.object(self.app, 'call') as call:
            self.app.start_auto()
            call.assert_not_called()
        event = {'seq': 1, 'kind': 'error', 'text': '[model_not_found] 模型 active-model 不存在'}
        self.app.show_automation({'automation': {'running': False, 'phase': 'error', 'error': 'model_not_found',
                                               'message': '模型 active-model 不存在'}, 'events': [event], 'cursor': 1})
        self.assertIn('[model_not_found]', self.app.status.get())
        self.assertIn('[model_not_found]', self.app.output.get('1.0', 'end'))
        self.assertIn('[model_not_found]', self.app.model_output.get('1.0', 'end'))
        self.assertEqual(self.app.model.get(), 'next-model')


if __name__ == '__main__':
    unittest.main()
