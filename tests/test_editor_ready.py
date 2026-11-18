"""Readiness checks using metadata and synthetic in-memory editor content."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import Engine, ToolError


class TestEditorReady(unittest.TestCase):
    def setUp(self):
        self.engine = Engine()
        self.loading = {'description': 'fixture description', 'files': ['dir/file'],
                        'current_path': '', 'editor_available': False}
        self.ready = dict(self.loading, current_path='dir/file', editor_available=True, content='fixture value')

    def test_explicit_file_can_be_opened_before_any_editor_exists(self):
        button = Mock()
        with patch.object(self.engine, 'snapshot', side_effect=[self.loading, self.ready]), \
                patch.object(self.engine, 'js', return_value=button) as js, patch.object(self.engine, 'sleep'):
            result = self.engine.select_file('dir/file')
        self.assertEqual(result, self.ready)
        js.assert_called_once_with('select_file', path='dir/file')
        button.click.assert_called_once()

    def test_auto_context_waits_for_file_tree_then_editor(self):
        initial = dict(self.loading, files=[])
        button = Mock()
        with patch.object(self.engine, 'snapshot', side_effect=[initial, self.loading, self.ready]), \
                patch.object(self.engine, 'js', return_value=button), patch.object(self.engine, 'sleep'):
            result = self.engine.wait_snapshot(code=True)
        self.assertEqual(result['content'], 'fixture value')
        button.click.assert_called_once()

    def test_file_not_reclicked_while_editor_is_loading(self):
        button = Mock()
        with patch.object(self.engine, 'snapshot', side_effect=[self.loading, self.loading, self.ready]), \
                patch.object(self.engine, 'js', return_value=button), patch.object(self.engine, 'sleep'):
            self.engine.select_file('dir/file')
        button.click.assert_called_once()

    def test_editor_timeout_returns_file_and_notice_metadata(self):
        with patch.object(self.engine, 'wait_snapshot', return_value=self.loading), \
                patch('core.time.monotonic', side_effect=[0, 46]), \
                patch.object(self.engine, 'js', return_value={'dialogs': ['fixture notice']}):
            with self.assertRaises(ToolError) as error:
                self.engine.select_file('dir/file')
        self.assertEqual(error.exception.code, 'file_not_open')
        self.assertEqual(error.exception.details['available_files'], ['dir/file'])


if __name__ == '__main__':
    unittest.main()
