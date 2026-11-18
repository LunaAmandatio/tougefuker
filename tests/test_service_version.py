"""Tool-only checks: no browser launches, no experiment source execution."""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from educoder_agent.core import BASE, DEFAULT_TASK, Engine
from educoder_agent.service import build_id, ensure_server, remove_runtime
from educoder_agent.paths import ProjectPaths


class TestServiceVersion(unittest.TestCase):
    def temporary_base(self):
        root = BASE / '.state' / 'tests'
        root.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(temp.cleanup)
        return Path(temp.name)

    def test_current_backend_is_reused_without_restart(self):
        base = self.temporary_base()
        version = build_id(base)
        with patch('educoder_agent.service.rpc', return_value={'ok': True, 'backend_version': version}) as rpc, patch('educoder_agent.service.subprocess.Popen') as spawn:
            result = ensure_server(base)
            self.assertFalse(result['updated'])
            rpc.assert_called_once()
            spawn.assert_not_called()

    def test_legacy_backend_without_version_is_replaced(self):
        base = self.temporary_base()
        version = build_id(base)
        responses = [{'ok': True, 'service': 'educoder-agent'}, {'ok': True},
                     {'ok': True, 'backend_version': version}]
        with patch('educoder_agent.service.rpc', side_effect=responses) as rpc, patch('educoder_agent.service.time.sleep'), patch('educoder_agent.service.subprocess.Popen') as spawn:
            result = ensure_server(base)
            self.assertTrue(result['updated'])
            self.assertEqual([call.args[0] for call in rpc.call_args_list], ['ping', 'shutdown', 'ping'])
            spawn.assert_called_once()

    def test_editing_adapter_changes_backend_fingerprint(self):
        base = self.temporary_base()
        before = build_id(base)
        script = ProjectPaths(base).browser_script
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text('// tool adapter revision', encoding='utf-8')
        self.assertNotEqual(before, build_id(base))

    def test_windows_busy_runtime_delete_is_retried(self):
        from unittest.mock import Mock
        path = Mock()
        path.unlink.side_effect = [PermissionError('busy'), None]
        with patch('educoder_agent.service.time.sleep'):
            remove_runtime(path)
        self.assertEqual(path.unlink.call_count, 2)

    def test_context_from_classroom_opens_configured_task(self):
        engine = Engine()
        engine.browser = SimpleNamespace(current_url='https://www.educoder.net/classrooms/J2F8SUKL/shixun_homework')
        with patch.object(engine, 'selection', return_value={'restricted': False, 'ids': []}), patch.object(engine, 'navigate') as navigate, patch.object(engine, 'wait_snapshot', return_value={'description': 'tool fixture', 'editor_available': False}), patch.object(engine.store, 'context', return_value={'task_id': 'fixture'}):
            result = engine.context()
            navigate.assert_called_once_with(engine.config.get('task_url', DEFAULT_TASK))
            self.assertEqual(result['task_id'], 'fixture')


if __name__ == '__main__':
    unittest.main()
