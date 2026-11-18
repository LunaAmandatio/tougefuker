"""Real HTTP/backend upgrade check, with no browser or experiment-code execution."""
import json
import secrets
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import BASE, load_json, write_json
from service import build_id, ensure_server, remove_runtime, rpc
import service


class TestBackendUpgradeIntegration(unittest.TestCase):
    def test_real_legacy_upgrade_and_logs_command(self):
        parent = BASE / '.state' / 'tests'
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            base = Path(directory)
            # Copy only this tool's implementation; no lab files or browser profiles.
            for name in ('edu.py', 'core.py', 'service.py', 'diagnostics.py', 'browser.js', 'config.json',
                         'automation.py', 'codex_solver.py', 'solution.schema.json'):
                shutil.copyfile(BASE / name, base / name)
            runtime_path = base / '.state' / 'service.json'

            class LegacyHandler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_POST(self):
                    request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                    payload = json.dumps({'ok': True, 'service': 'educoder-agent'}).encode()
                    self.send_response(200)
                    self.send_header('Content-Length', str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    if request['command'] == 'shutdown':
                        remove_runtime(runtime_path)
                        threading.Thread(target=legacy.shutdown, daemon=True).start()

            legacy = ThreadingHTTPServer(('127.0.0.1', 0), LegacyHandler)
            thread = threading.Thread(target=legacy.serve_forever, daemon=True)
            thread.start()
            write_json(runtime_path, {'port': legacy.server_port, 'token': secrets.token_urlsafe(16)})
            try:
                result = ensure_server(base)
                self.assertTrue(result['updated'])
                self.assertEqual(rpc('ping', base=base)['backend_version'], build_id(base))
                failure = rpc('unknown_tool_operation', base=base)
                self.assertEqual(failure['error'], 'invalid_command')
                self.assertIn('error_id', failure)
                logs = rpc('logs', {'tail': 1}, base=base)
                self.assertTrue(logs['ok'])
                self.assertEqual(logs['entries'][0]['error'], 'invalid_command')
                self.assertEqual(logs['backend_version'], result['backend_version'])
                config = load_json(base / 'config.json', {})
                write_json(base / '.state' / 'course.json', {'course_url': config['course_url'],
                    'experiments': [{'id': '4115214', 'title': 'fixture'}]})
                selected = rpc('select', {'ids': ['4115214']}, base=base)
                self.assertTrue(selected['ok'])
                self.assertEqual(rpc('status', base=base)['selection']['ids'], ['4115214'])
                self.assertEqual(rpc('select', base=base)['selection']['ids'], ['4115214'])
                self.assertEqual(rpc('select', {'ids': []}, base=base)['selection'], {'restricted': True, 'ids': []})
                self.assertFalse(rpc('auto-status', base=base)['automation']['running'])
                self.assertTrue(rpc('auto-start', base=base)['ok'])
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    automatic = rpc('auto-status', base=base)['automation']
                    if not automatic['running']:
                        break
                    time.sleep(0.03)
                self.assertEqual(automatic['error'], 'empty_selection')
                self.assertTrue(rpc('auto-stop', base=base)['ok'])
            finally:
                legacy.shutdown()
                legacy.server_close()
                thread.join(timeout=2)
                try:
                    rpc('shutdown', base=base, timeout=2)
                except Exception:
                    pass
                deadline = time.monotonic() + 6
                while load_json(runtime_path, {}) and time.monotonic() < deadline:
                    time.sleep(0.05)
                # The child releases files immediately after removing its runtime entry.
                time.sleep(0.15)
                for process in service._background_services:
                    if str(base / 'edu.py') in process.args:
                        process.wait(timeout=3)


if __name__ == '__main__':
    unittest.main()
