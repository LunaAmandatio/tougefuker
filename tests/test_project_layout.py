"""Verify relocated resources, source entry points and backend upgrade detection."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from educoder_agent.core import Engine, write_json
from educoder_agent.paths import BASE, ProjectPaths
from educoder_agent.service import build_id


class TestProjectLayout(unittest.TestCase):
    def setUp(self):
        directory = BASE / '.state' / 'tests'
        directory.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_cli_launcher_works_from_another_working_directory(self):
        result = subprocess.run([sys.executable, str(BASE / 'edu.py'), '--help'],
                                cwd=self.root, capture_output=True, text=True, encoding='utf-8', timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('auto-start', result.stdout)
        self.assertIn('input', result.stdout)
        self.assertFalse((self.root / '.state').exists())

    def test_engine_uses_project_config_assets_and_existing_runtime_location(self):
        paths = ProjectPaths(self.root)
        write_json(paths.config, {'page_timeout': 17})
        paths.browser_script.parent.mkdir(parents=True, exist_ok=True)
        paths.browser_script.write_text('// synthetic adapter fixture', encoding='utf-8')
        engine = Engine(self.root)
        self.assertEqual(engine.config['page_timeout'], 17)
        self.assertEqual(engine.script, '// synthetic adapter fixture')
        self.assertEqual(engine.root, self.root / '.state')
        self.assertFalse((self.root / 'src' / '.state').exists())

    def test_backend_detects_package_and_config_edits_but_ignores_runtime_data(self):
        paths = ProjectPaths(self.root)
        before = build_id(self.root)
        source = self.root / 'src' / 'educoder_agent' / 'service.py'
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text('# synthetic service revision', encoding='utf-8')
        after_source = build_id(self.root)
        self.assertNotEqual(before, after_source)
        write_json(paths.config, {'codex': {'model': 'fixture-model'}})
        after_config = build_id(self.root)
        self.assertNotEqual(after_source, after_config)
        write_json(paths.state / 'ui.json', {'model': 'next-fixture-model'})
        self.assertEqual(after_config, build_id(self.root))


if __name__ == '__main__':
    unittest.main()
